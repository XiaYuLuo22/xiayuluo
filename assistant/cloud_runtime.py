"""Opt-in text synchronization. No cloud account creation or task execution."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class CloudSyncRuntime:
    INTERVAL = 30

    def __init__(self, settings, memory, *, ready=lambda: True):
        self.ready = ready
        self.unsyncable_count = 0
        self.configured = bool(settings.cloud_sync_url and settings.cloud_sync_device_token)
        self.phase = 'off'
        self.last_sync = None
        self.message = '未配置独立云服务；当前仍为电脑主机同步，不会上传私人数据。'
        self.task = None
        self.replica = None
        self.client = None
        self.bridge = None
        self.closed = False
        self.paused = False
        self.guard = asyncio.Lock()
        if self.configured:
            from .cloud_client import CloudClient
            from .cloud_replica import CloudReplica
            from .cloud_bridge import MemoryCloudBridge
            try:
                client = CloudClient(settings.cloud_sync_url, settings.cloud_sync_device_token)
                self.replica = CloudReplica(Path(settings.data_dir) / 'cloud-replica.sqlite3')
                self.replica.bind_origin(client.origin)
                self.bridge = MemoryCloudBridge(memory, self.replica, client)
                self.client = client
                self.phase = 'waiting'
                self.message = '已配置文本同步；实际连接以同步结果为准，不包含活动截图或训练数据。'
            except (OSError, ValueError, sqlite3.Error):
                if self.replica is not None:
                    self.replica.close()
                    self.replica = None
                self.client = self.bridge = None
                self.phase = 'blocked'
                self.message = '云地址或本地同步库无法安全核实；已停止连接并保留原库，聊天仍可使用，请核实原配置。'

    def status(self):
        ready = self.ready()
        return {'configured': self.configured, 'phase': self.phase, 'last_sync': self.last_sync,
                'usable': self.bridge is not None,
                'startup_ready': ready, 'unsyncable_count': self.unsyncable_count,
                'interval_seconds': self.INTERVAL, 'message': self.message if ready else '等待更新助手确认本次启动，暂不进行后台数据写入。',
                'scope': 'sessions-messages-persistent-memories', 'executes_tasks': False}

    async def sync_now(self):
        if not self.ready():
            raise ValueError('等待更新助手确认本次启动，暂不进行云同步写入。')
        if not self.bridge or self.closed or self.paused:
            raise ValueError(self.message if self.phase == 'blocked' else '尚未配置可用的独立云同步服务。')
        if self.guard.locked():
            raise ValueError('已有云同步正在进行，请稍后核实状态。')
        async with self.guard:
            self.phase = 'syncing'
            try:
                result = await self.bridge.sync_once()
                conflicts = self.bridge.conflicts()
                self.unsyncable_count = int(result.get('unsyncable_count', 0)) if result else 0
                self.last_sync = datetime.now(timezone.utc).isoformat()
                remaining = bool(result and (result.get('more') or result.get('pending')))
                self.phase = 'conflict' if conflicts else 'waiting' if remaining else 'ready'
                self.message = ('同步存在冲突，原始内容与待发送记录保留，需处理后重试。' if conflicts
                                else '本批同步完成，仍有数据待同步；下次继续，不代表全部同步完成。' if remaining
                                else '本次文本同步完成；未上传截图、活动摘要、训练数据或登录凭据。')
                if self.unsyncable_count:
                    if not conflicts:
                        self.phase = 'partial'
                    self.message += f' 有 {self.unsyncable_count} 条记录无法同步，原文完整保留在本机；其他正常记录继续处理。'
            except asyncio.CancelledError:
                self.phase = 'paused'
                self.message = '云同步已暂停；本地数据和未确认队列保留。'
                raise
            except Exception as error:
                self.phase = 'error'
                self.message = '云同步失败；本地数据和未确认队列保留，请检查服务、凭据或冲突。'
                # Never surface server bodies, URLs with credentials, or token values.
                raise ValueError(self.message) from error
            return self.status()

    async def _run(self):
        while not self.closed:
            with suppress(ValueError):
                await self.sync_now()
            await asyncio.sleep(self.INTERVAL)

    async def start(self):
        if self.bridge is not None and self.configured and not self.closed and (self.task is None or self.task.done()):
            self.paused = False
            self.task = asyncio.create_task(self._run())

    async def pause(self):
        self.paused = True
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        # A manually requested sync can also own the bridge. Do not close its
        # SQLite connection underneath it or restart the App mid-import.
        async with self.guard:
            if self.bridge is not None and self.configured:
                self.phase = 'paused'
                self.message = '云同步已暂停；本地数据和未确认队列保留。'

    async def close(self):
        self.closed = True
        await self.pause()
        if self.replica is not None:
            self.replica.close()

