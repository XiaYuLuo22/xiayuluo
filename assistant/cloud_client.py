"""Opt-in HTTPS device client. This module does not deploy or discover a cloud."""
import json
import re
from urllib.parse import urlsplit

import httpx


MAX_DOCUMENT = 1024 * 1024
_IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class CloudSyncError(ValueError):
    pass


class CloudConflict(CloudSyncError):
    """The pending local operation must be retained for explicit reconciliation."""


def cloud_origin(raw):
    if not isinstance(raw, str):
        raise CloudSyncError('云同步地址必须是 HTTPS 源站。')
    value = urlsplit(raw.strip())
    try:
        invalid_port = value.port is not None and not 1 <= value.port <= 65535
    except ValueError:
        invalid_port = True
    if (value.scheme != 'https' or not value.hostname or value.username or value.password
            or value.query or value.fragment or value.path not in ('', '/') or invalid_port):
        raise CloudSyncError('云同步地址必须是 HTTPS 源站，不能包含凭据、路径或查询参数。')
    return raw.strip().rstrip('/')


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CloudSyncError('云同步记录编号无效。')
    return value


class CloudClient:
    def __init__(self, origin, device_token, *, transport=None):
        self.origin = cloud_origin(origin)
        if (not isinstance(device_token, str) or not 20 <= len(device_token) <= 512
                or any(ord(char) <= 32 or ord(char) >= 127 for char in device_token)):
            raise CloudSyncError('云同步设备凭据无效。')
        self._device_token = device_token
        self.transport = transport

    def __repr__(self):
        return f'CloudClient(origin={self.origin!r}, device_token=<redacted>)'

    async def request(self, method, path, body=None):
        if not path.startswith('/sync/v1/') or '..' in path or '#' in path:
            raise CloudSyncError('云同步请求路径无效。')
        content = None
        if body is not None:
            content = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            if len(content) > MAX_DOCUMENT:
                raise CloudSyncError('云同步请求过大，待发送内容已保留。')
        try:
            async with httpx.AsyncClient(transport=self.transport, follow_redirects=False,
                                         timeout=httpx.Timeout(20, connect=10), trust_env=False) as client:
                async with client.stream(method, self.origin + path, content=content,
                        headers={'Authorization': 'Bearer ' + self._device_token,
                                 'Content-Type': 'application/json', 'Accept': 'application/json'}) as response:
                    if response.status_code == 409:
                        raise CloudConflict('云端版本冲突；本地待发送内容已保留，需要核对后处理。')
                    if response.status_code != 200 and response.status_code != 201:
                        raise CloudSyncError(f'云同步暂未完成（HTTP {response.status_code}）；不会跳转或自动更换服务。')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_DOCUMENT:
                            raise CloudSyncError('云同步响应过大，已停止读取。')
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except httpx.HTTPError:
            raise CloudSyncError('云同步网络不可用；待发送内容保留，不会改用其他服务。') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CloudSyncError('云同步响应格式无效；本地内容保留。') from None

    async def push_events(self, events):
        return await self.request('POST', '/sync/v1/events', {'events': events})

    async def pull_events(self, after=0, *, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise CloudSyncError('云同步游标或分页大小无效。')
        return await self.request('GET', f'/sync/v1/events?after={after}&limit={limit}')

    async def submit_task(self, task):
        return await self.request('POST', '/sync/v1/tasks', task)

    async def get_task(self, task_id):
        return await self.request('GET', '/sync/v1/tasks/' + _identifier(task_id))
