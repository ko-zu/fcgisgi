# fcgisgi

A FastCGI to ASGI/WSGI adapter using Python's `asyncio`.

## Features
-   FastCGI to ASGI adapter.
-   FastCGI to WSGI adapter (via thread pool).
-   `asyncio` based server implementation.
-   Sans-IO FastCGI protocol implementation.

## Installation
(Once published)
```bash
pip install fcgisgi
```

## Usage

### ASGI
```python
import asyncio
from fcgisgi import run_asgi_server

async def app(scope, receive, send):
    assert scope['type'] == 'http'
    await send({
        'type': 'http.response.start',
        'status': 200,
        'headers': [
            (b'content-type', b'text/plain'),
        ],
    })
    await send({
        'type': 'http.response.body',
        'body': b'Hello, world!',
    })

if __name__ == "__main__":
    # Bind to a TCP port or a Unix socket
    asyncio.run(run_asgi_server(app, bind_address=("127.0.0.1", 9000)))
```

### WSGI
```python
import asyncio
from fcgisgi import run_wsgi_server

def app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b"Hello, world!"]

if __name__ == "__main__":
    asyncio.run(run_wsgi_server(app, bind_address=("127.0.0.1", 9000)))
```


### Using mod_fcgid

When using Apache with `mod_fcgid`, the `SCRIPT_NAME` parameter often includes the FastCGI script filename (e.g., `/index.fcgi`), which can interfere with the routing of your application.

Use the `force_script_name` option to override the mount point (root path) of your application, ensuring the routing engine receives the correct path.

```shell
### .htaccess 
AddHandler fcgid-script .fcgi
RewriteEngine On
RewriteBase /
# static files
RewriteRule ^(static|assets|.well-known)/ - [L]
RewriteRule ^(favicon.ico|robots.txt)$ - [L]
# route all to fcgi script
RewriteRule ^(.*)$ index.fcgi/$1 [QSA,END]

### index.fcgi
#!/bin/sh
exec /path/to/venv/bin/python entrypoint.py
```

```python
import asyncio
from fcgisgi import run_asgi_server

async def app(scope, receive, send):
    """ SNIP """

if __name__ == "__main__":
    # Specify the mount point (e.g., "/" if mounted at the root)
    asyncio.run(run_asgi_server(app, force_script_name="/"))

```

## License
MIT
