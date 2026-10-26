import json

import httpx
import pytest

from assistant.cloud_client import CloudClient, CloudConflict, CloudSyncError, cloud_origin


@pytest.mark.parametrize('url', ['http://localhost', 'https://u:p@example.com', 'https://example.com/a',
    'https://example.com?token=secret', 'https://example.com/#secret', 'file:///tmp/private'])
def test_cloud_origin_never_accepts_plaintext_or_embedded_credentials(url):
    with pytest.raises(ValueError):
        cloud_origin(url)


async def test_device_token_stays_on_explicit_origin_and_is_not_in_repr():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer ' + 'private-device-token-123456'
        assert request.url.host == 'cloud.example'
        return httpx.Response(200, json={'events': [], 'cursor': 0, 'more': False})
    client = CloudClient('https://cloud.example/', 'private-device-token-123456', transport=httpx.MockTransport(handler))
    assert 'private-device' not in repr(client)
    assert (await client.pull_events())['cursor'] == 0
    assert len(requests) == 1


async def test_redirect_conflict_bad_json_and_oversize_have_safe_errors():
    responses = [httpx.Response(302, headers={'location': 'https://attacker.example'}),
                 httpx.Response(409, json={'detail': 'secret-bearing error'}),
                 httpx.Response(200, content=b'invalid private data'),
                 httpx.Response(200, content=b'x' * (1024 * 1024 + 1))]
    calls = []
    def handler(request):
        calls.append(request)
        return responses.pop(0)
    client = CloudClient('https://cloud.example', 'device-token-0123456789012345', transport=httpx.MockTransport(handler))
    for exception in (CloudSyncError, CloudConflict, CloudSyncError, CloudSyncError):
        with pytest.raises(exception) as error:
            await client.pull_events()
        assert 'secret-bearing' not in str(error.value) and 'private data' not in str(error.value)
    assert len(calls) == 4 and all(call.url.host == 'cloud.example' for call in calls)


async def test_request_body_and_task_route_are_explicit_and_bounded():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    client = CloudClient('https://cloud.example', 'device-token-0123456789012345', transport=httpx.MockTransport(handler))
    await client.submit_task({'id': 'task-1', 'prompt': 'explicit text'})
    assert json.loads(calls[0].content)['prompt'] == 'explicit text'
    with pytest.raises(ValueError):
        await client.get_task('../secret')
    with pytest.raises(ValueError):
        await client.push_events([{'content': 'a' * 1100000}])
    assert len(calls) == 1
