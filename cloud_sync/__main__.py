"""Bind only loopback; a separately configured TLS proxy publishes sync routes."""
import os
from pathlib import Path

import uvicorn

from .server import create_app


def main():
    secret = os.environ.get('XIAYULUO_CLOUD_ADMIN_TOKEN', '')
    database = os.environ.get('XIAYULUO_CLOUD_DATABASE', '')
    hosts = os.environ.get('XIAYULUO_CLOUD_HOSTS', '').split(',')
    hosts = tuple(value.strip() for value in hosts if value.strip())
    if not database or not Path(database).is_absolute() or not hosts:
        raise SystemExit('Set absolute XIAYULUO_CLOUD_DATABASE, XIAYULUO_CLOUD_HOSTS and a private random XIAYULUO_CLOUD_ADMIN_TOKEN.')
    app = create_app(database, secret, allowed_hosts=hosts)
    uvicorn.run(app, host='127.0.0.1', port=8877, proxy_headers=True,
                forwarded_allow_ips='127.0.0.1', access_log=False)


if __name__ == '__main__':
    main()
