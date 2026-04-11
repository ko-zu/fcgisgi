# fcgisgi

A FastCGI-to-ASGI/WSGI adapter powered by Python's `asyncio`.

## Features
-   FastCGI-to-ASGI adapter.
-   FastCGI-to-WSGI adapter (via thread pool).
-   `asyncio`-based server implementation.
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
### .htaccess 
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
### index.fcgi
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
    # Specify the mount point (e.g., "" if mounted at the root)
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

- **ASGI**: `scope['extensions']['fcgisgi']['fcgi_params']` in the `http.request` scope.
- **WSGI**: `environ['fcgisgi.fcgi_params']` in the `environ` dictionary.


## References
- ASGI Specification: https://asgi.readthedocs.io/
- WSGI Specification (PEP 3333): https://peps.python.org/pep-3333/
- FastCGI Specification: https://fastcgi-archives.github.io/FastCGI_Specification.html

## License
MIT
