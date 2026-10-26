"""Opt-in, fenced execution of explicit cloud Codex tasks, never sync messages.

Lease credentials live only in memory. An uncertain task is recorded locally and
is never automatically replayed, including after process restart. The cloud can
expire that lease; the owner must inspect possible side effects before resubmitting.
"""
import asyncio
from contextlib import aclosing, suppress
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import RLock
import time


IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
MAX_RESULT = 64000


class TaskInterrupted(ValueError):
    pass


class CloudTaskWorker:
    def __init__(self, client, codex, memory, *, data_dir, eligible=lambda: True, before_execute=None,
                 poll_interval=5, heartbeat_interval=20, clock=time.time):
        if not .01 <= poll_interval <= 60 or not .01 <= heartbeat_interval <= 30:
            raise ValueError('云任务检查和保活间隔无效。')
        self.client, self.codex, self.memory = client, codex, memory
        self.path = Path(data_dir).absolute() / 'cloud-tasks.sqlite3'
        self.eligible, self.clock = eligible, clock
        self.before_execute = before_execute
        self.poll_interval, self.heartbeat_interval = poll_interval, heartbeat_interval
        self.enabled = self.paused = self.closed = False
        self.generation = 0
        self.phase, self.message = 'off', '云任务执行默认关闭；同步文本不会触发电脑操作。'
        self.current_id = None
        self.scheduler = self.task = None
        self.db = None
        self.lock = RLock()
        self.next_check = 0

    def _open_journal(self):
        with self.lock:
            if self.db is not None:
                return
            for path in (self.path, *self.path.parents):
                if os.path.lexists(path):
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError('云任务记录目录不能使用链接或目录联接。')
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            created = False
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self.path.is_file() or (os.name == 'posix' and self.path.stat().st_mode & 0o077):
                    raise ValueError('云任务记录必须是本机私有普通文件，原文件保留。')
            else:
                os.close(descriptor)
                created = True
            db = sqlite3.connect(str(self.path), check_same_thread=False)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    if created:
                        db.executescript('''
                            CREATE TABLE worker_meta(schema INTEGER NOT NULL, application TEXT NOT NULL);
                            INSERT INTO worker_meta VALUES(1,'xiayuluo-cloud-tasks');
                            CREATE TABLE worker_tasks(
                                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                                prompt_hash TEXT NOT NULL, phase TEXT NOT NULL, outcome TEXT,
                                result TEXT NOT NULL DEFAULT '', confirmed INTEGER NOT NULL DEFAULT 0,
                                mirrored INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                        ''')
                    if [tuple(row) for row in db.execute('SELECT schema,application FROM worker_meta')] != [(1, 'xiayuluo-cloud-tasks')]:
                        raise ValueError('云任务记录格式不兼容，原文件保留。')
                    db.execute('PRAGMA journal_mode=WAL')
                    db.execute('PRAGMA synchronous=FULL')
                    db.execute("UPDATE worker_tasks SET phase='interrupted',updated_at=? WHERE phase IN ('claimed','start-pending','running','reporting')", (self.clock(),))
            except Exception:
                db.close()
                raise ValueError('云任务记录无法验证；不会覆盖或自动重建原记录。') from None
            self.db = db

    def _workspace(self):
        workspace = self.codex.workspace_status()
        if (not isinstance(workspace, dict) or not workspace.get('available') or not workspace.get('configured')
                or not isinstance(workspace.get('id'), str) or not IDENTIFIER.fullmatch(workspace['id'])
                or workspace.get('sandbox') not in ('read-only', 'workspace-write')):
            raise TaskInterrupted('电脑工作区或 Codex 尚未就绪，未领取任务。')
        if self.codex.approval_mode != 'auto' or workspace.get('approvals') != 'automatic':
            raise TaskInterrupted('当前为逐次确认模式，云任务不会自动领取或执行。')
        return workspace

    def _active(self, generation, workspace_id=None):
        if not self.enabled or self.paused or self.closed or self.generation != generation:
            raise TaskInterrupted('云任务已停止或被本机操作抢占；不自动重放。')
        if not self.eligible():
            raise TaskInterrupted('正在处理本机任务，云任务已让出执行权限。')
        current = self._workspace()
        if workspace_id is not None and current['id'] != workspace_id:
            raise TaskInterrupted('电脑工作区已变更，云任务已中断；不能移到其他目录执行。')
        return current

    @staticmethod
    def _cancel(task):
        if task and not task.done():
            def cancel():
                if not task.done() and not task.cancelling():
                    task.cancel()
            task.get_loop().call_soon_threadsafe(cancel)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('云任务开关必须是布尔值。')
        with self.lock:
            if enabled:
                if self.client is None or self.closed:
                    raise ValueError('请先配置独立云服务和电脑设备凭据；当前未启用云任务。')
                self._workspace()
                try:
                    self._open_journal()
                except (OSError, sqlite3.Error):
                    raise ValueError('无法建立本机私有云任务记录；原文件保留，未启用执行。') from None
            if enabled != self.enabled:
                self.enabled = enabled
                self.generation += 1
            if not enabled:
                self._cancel(self.task)
            self.phase = 'waiting' if enabled else 'off'
            self.message = ('已允许绑定工作区的明确 Codex 云任务；普通同步消息不会执行。' if enabled
                            else '云任务执行已关闭；未确认任务不会自动重放。')
            return self.status()

    def records(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('云任务记录数量无效。')
        with self.lock:
            if self.db is None:
                if not self.path.exists():
                    return []
                self._open_journal()
            return [dict(row) for row in self.db.execute('SELECT * FROM worker_tasks ORDER BY created_at DESC,id LIMIT ?', (limit,))]

    def status(self):
        with self.lock:
            uncertain = 0 if self.db is None else self.db.execute("SELECT COUNT(*) FROM worker_tasks WHERE confirmed=0 AND phase NOT IN ('claimed','start-pending','running','reporting')").fetchone()[0]
            return {'configured': self.client is not None, 'enabled': self.enabled, 'paused': self.paused,
                    'phase': self.phase, 'message': self.message, 'task_id': self.current_id,
                    'unconfirmed_count': uncertain, 'executes_tasks': self.enabled and not self.paused and not self.closed,
                    'scope': 'explicit-codex-task-current-workspace', 'replays_uncertain_tasks': False}

    def _record(self, task, phase, **values):
        with self.lock, self.db:
            if phase == 'claimed':
                self.db.execute('INSERT INTO worker_tasks(id,workspace_id,session_id,prompt_hash,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                                (task['id'], task['workspace_id'], task['session_id'], hashlib.sha256(task['prompt'].encode()).hexdigest(), phase, self.clock(), self.clock()))
            else:
                allowed = {'outcome', 'result', 'confirmed', 'mirrored'}
                if not set(values) <= allowed:
                    raise ValueError('Invalid internal task state.')
                assignments = ['phase=?', 'updated_at=?'] + [key + '=?' for key in values]
                self.db.execute('UPDATE worker_tasks SET ' + ','.join(assignments) + ' WHERE id=?',
                                (phase, self.clock(), *values.values(), task['id']))

    def _same_task(self, response, task, expected):
        value = response.get('task') if isinstance(response, dict) else None
        if (not isinstance(value, dict) or any(value.get(key) != task[key] for key in ('id', 'workspace_id', 'session_id', 'prompt'))
                or value.get('status') != expected):
            raise TaskInterrupted('云任务回执与本次任务不匹配，已停止执行。')
        return value

    def _deadline(self, value):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not self.clock() + 1 < value <= self.clock() + 300):
            raise TaskInterrupted('云任务租约无效或已过期，未继续执行。')
        return value

    def _claim(self, receipt, workspace_id):
        if not isinstance(receipt, dict) or 'task' not in receipt:
            raise TaskInterrupted('云任务领取回执无效。')
        task = receipt['task']
        if task is None:
            return None
        if (not isinstance(task, dict) or any(not isinstance(task.get(key), str) or not IDENTIFIER.fullmatch(task[key])
                                            for key in ('id', 'workspace_id', 'session_id'))
                or task['workspace_id'] != workspace_id or task.get('status') != 'leased'
                or not isinstance(task.get('prompt'), str) or not task['prompt'].strip() or len(task['prompt']) > 12000):
            raise TaskInterrupted('任务与电脑工作区或已授权文本任务不匹配，未执行。')
        token = receipt.get('lease_token')
        if not isinstance(token, str) or not 20 <= len(token) <= 512 or any(not 33 <= ord(char) <= 126 for char in token):
            raise TaskInterrupted('云任务租约凭据无效。')
        self._deadline(receipt.get('lease_expires_at'))
        self._deadline(task.get('lease_expires_at'))
        return task, token

    async def _codex_result(self, task, generation):
        self._active(generation, task['workspace_id'])
        parts, remaining = [], MAX_RESULT
        truncated = False
        async with aclosing(self.codex.events(task['prompt'])) as stream:
            async for event in stream:
                self._active(generation, task['workspace_id'])
                if not isinstance(event, dict):
                    raise ValueError('Invalid Codex event.')
                if event.get('type') == 'error':
                    raise ValueError('Codex reported a failure.')
                if event.get('type') == 'text':
                    text = event.get('text')
                    if not isinstance(text, str):
                        raise ValueError('Invalid Codex output.')
                    truncated = truncated or len(text) > remaining
                    if remaining:
                        parts.append(text[:remaining])
                    remaining = max(0, remaining - len(text))
        result = ''.join(parts).strip() or 'Codex 任务已完成。'
        if truncated:
            note = '\n[结果过长，仅保留前一部分；请在电脑核实完整变更。]'
            result = result[:MAX_RESULT - len(note)] + note
        return result

    async def _heartbeats(self, task, token, generation, lease):
        while True:
            remaining = lease[0] - self.clock()
            if remaining <= 1:
                raise TaskInterrupted('云任务租约已过期，已停止本次执行。')
            await asyncio.sleep(min(self.heartbeat_interval, remaining / 2))
            self._active(generation, task['workspace_id'])
            async with asyncio.timeout(min(20, max(.01, lease[0] - self.clock() - 1))):
                response = await self.client.heartbeat_task(task['id'], token)
            running = self._same_task(response, task, 'running')
            lease[0] = self._deadline(running.get('lease_expires_at'))

    def _mirror(self, task, result, outcome):
        # Importing a task result is not a new user request or memory promotion.
        if self.memory is None:
            return
        # Message events have a 64,000 UTF-8 BYTE payload budget, whereas task
        # results allow 64,000 Unicode characters. Keep a bounded chat preview;
        # the complete result remains in the task endpoint and private journal.
        preview = result
        if len(preview) > 12000:
            preview = preview[:12000] + '\n[会话仅显示结果预览；完整结果见云任务或本机任务记录。]'
        with self.memory.lock, self.memory.db:
            if not self.memory.has_session(task['session_id']):
                return
            from .memory import now
            self.memory.db.execute('INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)',
                                   (task['session_id'], 'user', '[云端明确任务 · ' + task['id'] + '] ' + task['prompt'], now()))
            self.memory.db.execute('INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)',
                                   (task['session_id'], 'assistant', '[云任务' + ('完成' if outcome == 'succeeded' else '未完成') + '] ' + preview, now()))
        self._record(task, outcome, outcome=outcome, result=result, confirmed=1, mirrored=1)

    async def _execute(self, generation):
        task = None
        execution = heartbeat = None
        recorded = False
        try:
            workspace = self._active(generation)
            receipt = await self.client.claim_task(workspace['id'])
            self._active(generation, workspace['id'])
            claimed = self._claim(receipt, workspace['id'])
            if claimed is None:
                return None
            task, token = claimed
            with self.lock:
                if self.db.execute('SELECT 1 FROM worker_tasks WHERE id=?', (task['id'],)).fetchone():
                    self.phase, self.message = 'attention', '该任务已有本机记录，未重复执行；请核实先前结果。'
                    return None
                self.current_id = task['id']
                self._record(task, 'claimed'); recorded = True
            self._active(generation, workspace['id'])
            if self.before_execute is not None:
                # Explicit tasks may preempt a background observer, but an idle
                # queue must not repeatedly interrupt ordinary activity sensing.
                await self.before_execute()
                self._active(generation, workspace['id'])
            self._record(task, 'start-pending')
            response = await self.client.start_task(task['id'], token)
            running = self._same_task(response, task, 'running')
            lease = [self._deadline(running.get('lease_expires_at'))]
            self._active(generation, workspace['id'])
            self._record(task, 'running')
            self.phase, self.message = 'running', '正在本机绑定工作区执行明确的 Codex 云任务。'
            execution = asyncio.create_task(self._codex_result(task, generation))
            heartbeat = asyncio.create_task(self._heartbeats(task, token, generation, lease))
            done, _ = await asyncio.wait((execution, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                heartbeat.result()  # Any completion is loss of continued lease authority.
                raise TaskInterrupted('云任务保活意外终止。')
            outcome = 'succeeded'
            try:
                result = execution.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                outcome, result = 'failed', 'Codex 执行失败，操作可能已发生，请在电脑核实；不会自动重试。'
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            self._active(generation, workspace['id'])
            self._record(task, 'reporting', outcome=outcome, result=result)
            self.phase, self.message = 'reporting', '电脑任务已结束，正在核实云端结果回执；尚未确认送达。'
            try:
                self._deadline(lease[0])
                async with asyncio.timeout(min(20, max(.01, lease[0] - self.clock() - 1))):
                    response = await self.client.finish_task(task['id'], token, outcome, result)
                finished = self._same_task(response, task, outcome)
                if finished.get('result') != result:
                    raise TaskInterrupted('云任务结果回执不匹配。')
            except asyncio.CancelledError:
                raise
            except Exception:
                self._record(task, 'result-unconfirmed', outcome=outcome, result=result)
                self.phase, self.message = 'attention', '本机结果已保留，但云端未确认；不会自动重放任务，请核实。'
                return {'task_id': task['id'], 'phase': 'result-unconfirmed'}
            self._record(task, outcome, outcome=outcome, result=result, confirmed=1)
            try:
                self._mirror(task, result, outcome)
            except Exception:
                self.phase, self.message = 'attention', '云端已确认结果，但本机会话未能写入；本机结果记录保留，不重放。'
                return {'task_id': task['id'], 'phase': outcome, 'local_history': False}
            self.phase, self.message = 'waiting', '本次云任务结果已由云端确认，等待新的明确任务。'
            return {'task_id': task['id'], 'phase': outcome}
        except asyncio.CancelledError:
            if task and recorded:
                self._record(task, 'interrupted')
            raise
        except Exception:
            if task and recorded:
                self._record(task, 'interrupted')
            self.phase, self.message = 'attention', '云任务未完成或租约/工作区已变化；原记录保留，不自动重试。'
            self.next_check = self.clock() + max(30, self.poll_interval)
            return None
        finally:
            for child in (execution, heartbeat):
                if child and not child.done() and not child.cancelling(): child.cancel()
            for child in (execution, heartbeat):
                if child:
                    with suppress(asyncio.CancelledError, Exception): await child
            self.current_id = None

    async def tick(self):
        with self.lock:
            if self.task and not self.task.done():
                return None
            if not self.enabled or self.paused or self.closed or self.client is None or self.clock() < self.next_check:
                return None
            try:
                self._active(self.generation)
            except Exception:
                self.phase, self.message = 'waiting', '等待本机空闲、自动执行策略和原绑定工作区就绪；未领取云任务。'
                return None
            if self.codex.lock.locked():
                return None
            self.next_check = self.clock() + self.poll_interval
            task = self.task = asyncio.create_task(self._execute(self.generation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._cancel(task)
            with suppress(asyncio.CancelledError, Exception): await asyncio.shield(task)
            if asyncio.current_task().cancelling(): raise
            return None
        finally:
            with self.lock:
                if self.task is task: self.task = None

    async def interrupt_and_wait(self):
        with self.lock:
            self.generation += 1
            task = self.task
            self._cancel(task)
        if task:
            with suppress(asyncio.CancelledError, Exception): await asyncio.shield(task)
        self.phase = 'waiting' if self.enabled and not self.paused else 'off'
        self.message = '已让出云任务执行，优先处理本机操作；不重放已领取任务。'

    async def _schedule(self):
        while not self.closed:
            await self.tick()
            await asyncio.sleep(self.poll_interval)

    async def start(self):
        if self.closed: return
        self.paused = False
        if self.client is not None and (self.scheduler is None or self.scheduler.done()):
            self.scheduler = asyncio.create_task(self._schedule())

    async def pause(self):
        self.paused = True
        await self.interrupt_and_wait()
        if self.scheduler:
            self.scheduler.cancel()
            with suppress(asyncio.CancelledError): await self.scheduler
            self.scheduler = None

    async def close(self):
        self.closed = True
        await self.pause()
        with self.lock:
            if self.db is not None:
                self.db.close(); self.db = None
