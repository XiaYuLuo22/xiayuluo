"""HTTPS-only, single-owner private synchronization service.

Run behind a TLS reverse proxy, never publish the desktop's local /api routes.
No endpoint executes a task: the cloud only holds text and a fenced queue.
"""
from contextlib import asynccontextmanager
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .schema import (ClaimRequest, DeviceRequest, EventBatch, Identifier,
                     LeaseRequest, MAX_DOCUMENT, ResultRequest, TaskRequest)
from .store import Conflict, Store, token_hash


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        data = bytearray()
        while True:
            item = await receive()
            if item['type'] == 'http.disconnect':
                return
            data.extend(item.get('body', b''))
            if len(data) > MAX_DOCUMENT:
                return await JSONResponse({'detail': 'Request exceeds the text sync size limit.'}, status_code=413)(scope, receive, send)
            if not item.get('more_body', False):
                break
        consumed = False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': bytes(data), 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)


def create_app(database, admin_token, *, allowed_hosts=('localhost', '127.0.0.1', 'testserver'), clock=time.time):
    if (not isinstance(admin_token, str) or len(admin_token) < 32 or len(admin_token) > 512
            or any(ord(char) <= 32 or ord(char) >= 127 for char in admin_token)):
        raise ValueError('Provide a private random admin token of at least 32 ASCII characters.')
    admin_digest = token_hash(admin_token)
    if (not isinstance(allowed_hosts, (list, tuple)) or not allowed_hosts
            or any(not isinstance(host, str) or not host or '*' in host for host in allowed_hosts)):
        raise ValueError('Configure explicit cloud host names, not a wildcard.')
    store = Store(database, clock=clock)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            store.close()

    app = FastAPI(title='Xiayuluo private text sync', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.add_middleware(BodyLimit)

    @app.middleware('http')
    async def security(request, call_next):
        if request.url.path != '/healthz' and request.url.scheme != 'https':
            return JSONResponse({'detail': 'Cloud synchronization requires HTTPS.'}, status_code=426)
        if request.headers.get('origin'):
            return JSONResponse({'detail': 'Browser cross-origin access is not supported.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse({'detail': error.details}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Do not echo rejected credential/binary fields in a validation response.
        return JSONResponse({'detail': 'Invalid text synchronization request.'}, status_code=422)

    @app.exception_handler(PermissionError)
    async def permission_handler(request, error):
        return JSONResponse({'detail': 'Device has no permission for this workspace or operation.'}, status_code=403)

    def bearer(request):
        header = request.headers.get('authorization', '')
        return header[7:] if header.startswith('Bearer ') else ''

    def admin(request: Request):
        value = bearer(request)
        if not value or not secrets.compare_digest(token_hash(value), admin_digest):
            raise HTTPException(401, 'Administrator credentials required.')

    def device(request: Request):
        found = store.authenticate(bearer(request))
        if found is None:
            raise HTTPException(401, 'Device is unpaired or revoked.')
        return found

    @app.get('/healthz')
    def health():
        return {'ok': True}

    @app.post('/sync/v1/devices', dependencies=[Depends(admin)])
    def enroll(body: DeviceRequest):
        return store.create_device(body.model_dump())

    @app.get('/sync/v1/devices', dependencies=[Depends(admin)])
    def devices():
        return {'devices': store.devices()}

    @app.delete('/sync/v1/devices/{device_id}', dependencies=[Depends(admin)])
    def revoke(device_id: Identifier):
        if not store.revoke(device_id):
            raise HTTPException(404, 'Device not found.')
        return {'revoked': True}

    @app.post('/sync/v1/events')
    def push(body: EventBatch, owner=Depends(device)):
        return store.push(owner['id'], [event.model_dump() for event in body.events])

    @app.get('/sync/v1/device')
    def current_device(owner=Depends(device)):
        return {'device': owner}

    @app.get('/sync/v1/events')
    def pull(after: int = Query(default=0, ge=0, le=2**63-1),
             limit: int = Query(default=100, ge=1, le=100), owner=Depends(device)):
        return store.pull(after, limit)

    @app.post('/sync/v1/tasks')
    def submit(body: TaskRequest, owner=Depends(device)):
        return store.submit(owner['id'], body.model_dump())

    @app.get('/sync/v1/workspaces')
    def workspaces(owner=Depends(device)):
        return {'workspaces': store.workspaces()}

    @app.post('/sync/v1/tasks/claim')
    def claim(body: ClaimRequest, owner=Depends(device)):
        return store.claim(owner, body.workspace_id)

    @app.get('/sync/v1/tasks/{task_id}')
    def task(task_id: Identifier, owner=Depends(device)):
        result = store.task(task_id, owner)
        if result is None:
            raise HTTPException(404, 'Task not found.')
        return result

    @app.post('/sync/v1/tasks/{task_id}/start')
    def start(task_id: Identifier, body: LeaseRequest, owner=Depends(device)):
        return store.transition(task_id, owner, body.lease_token, 'start')

    @app.post('/sync/v1/tasks/{task_id}/heartbeat')
    def heartbeat(task_id: Identifier, body: LeaseRequest, owner=Depends(device)):
        return store.transition(task_id, owner, body.lease_token, 'heartbeat')

    @app.post('/sync/v1/tasks/{task_id}/result')
    def result(task_id: Identifier, body: ResultRequest, owner=Depends(device)):
        return store.transition(task_id, owner, body.lease_token, 'result', status=body.status, result=body.result)

    return app
