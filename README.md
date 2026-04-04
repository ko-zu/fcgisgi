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

## License
MIT
