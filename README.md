# fcgisgi

A FastCGI-to-ASGI/WSGI adapter.

Run ASGI/WSGI Python applications behind legacy FastCGI-based web hosting servers (e.g., Apache `mod_fcgid`).
An alternative to `flup.server.fcgi` package.

[![PyPI - Version](https://img.shields.io/pypi/v/fcgisgi)](https://pypi.org/project/fcgisgi/)

## Features
- FastCGI-to-ASGI adapter.
- FastCGI-to-WSGI adapter (via thread pool).
- `asyncio`-based server implementation.
- Support spawning from `mod_fcgid`.

## Installation
```shell
pip install fcgisgi
```

- PyPI: https://pypi.org/project/fcgisgi/
- GitHub: https://github.com/ko-zu/fcgisgi

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
    # asyncio.run(run_asgi_server(app, bind_address="/var/run/fcgisgi.sock"))

    # Alternatively, use the default FastCGI socket (fd=0) inherited from
    # the parent process (e.g., Apache mod_fcgid).
    # asyncio.run(run_asgi_server(app))
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

When using Apache with `mod_fcgid`, the `SCRIPT_NAME` parameter often includes the FastCGI script filename (e.g., `/index.fcgi`), which can interfere with your application's routing.

Use the `force_script_name` option to override the mount point (root path) of your application, ensuring the routing engine receives the expected path.

.htaccess
```shell
AddHandler fcgid-script .fcgi
RewriteEngine On
RewriteBase /
# Static files
RewriteRule ^(static|assets|\.well-known)/ - [L]
RewriteRule ^(favicon\.ico|robots\.txt)$ - [L]
# Route everything else to the fcgi script
RewriteRule ^.* index.fcgi/$0 [QSA,END]
```

index.fcgi
```shell
#!/bin/sh
PATH=/path/to/venv/bin:$PATH
export PATH
exec python entrypoint.py
```

entrypoint.py
```python
import asyncio
from fcgisgi import run_asgi_server

async def app(scope, receive, send):
    """ SNIP """

if __name__ == "__main__":
    # Specify the mount point.
    #     ""     if mounted at the root
    #     "/app" if mounted under "http://example.com/app/"
    asyncio.run(run_asgi_server(app, force_script_name=""))
```

## Configuration

You can pass additional configuration parameters to `run_asgi_server` or `run_wsgi_server` via keyword arguments:

- `startup_timeout` (float): Timeout for ASGI lifespan startup (default: `55.0`).
- `shutdown_timeout` (float): Timeout for graceful shutdown on SIGTERM (default: `55.0`).
- `max_workers` (int): Maximum number of worker threads for WSGI applications (passed to `ThreadPoolExecutor`).
- `force_script_name` (str): Override the `SCRIPT_NAME` (WSGI) or `root_path` (ASGI) parameter. Useful for normalizing routing behind a prefix.

Example:
```python
asyncio.run(run_asgi_server(
    app, 
    startup_timeout=30.0, 
    shutdown_timeout=30.0
))
```

## FCGI_PARAMS

The original `FCGI_PARAMS` passed from the web server can be retrieved as a list of `(bytes, bytes)` key-value pairs, preserving their original order and any duplicates.

- **ASGI**: `scope['extensions']['fcgisgi']['fcgi_params']` in the connection/request scope.
- **WSGI**: `environ['fcgisgi.fcgi_params']` in the `environ` dictionary.

### ASGI Middleware Example

You can use a middleware to expose custom FastCGI parameters (e.g., `RAW_URI`) as HTTP headers:

```python
class FCGIParamToHeaderMiddleware:
    def __init__(self, app, param_name=b"RAW_URI", header_name=b"x-fcgi-raw-uri"):
        self.app = app
        self.param_name = param_name
        self.header_name = header_name

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            fcgi_params = scope.get("extensions", {}).get("fcgisgi", {}).get("fcgi_params", [])
            for key, value in fcgi_params:
                if key == self.param_name:
                    scope["headers"].insert(0, (self.header_name, value))
                    break
        return await self.app(scope, receive, send)
```


## References
- ASGI Specification: https://asgi.readthedocs.io/
- WSGI Specification (PEP 3333): https://peps.python.org/pep-3333/
- FastCGI Specification: https://fastcgi-archives.github.io/FastCGI_Specification.html

## License
MIT
