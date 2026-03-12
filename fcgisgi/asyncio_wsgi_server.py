import asyncio
import socket
import os
from typing import Callable, Union, Tuple
from concurrent.futures import ThreadPoolExecutor

from .sansio import FCGI_LISTENSOCK_FILENO
from .wsgi_adapter import WSGIAdapter

class FastCGIWSGIProtocol(asyncio.Protocol):
    def __init__(self, wsgi_app: Callable, executor: ThreadPoolExecutor):
        self.wsgi_app = wsgi_app
        self.executor = executor
        self.adapter = None
        self.transport = None
        self.loop = None

    def connection_made(self, transport):
        self.transport = transport
        self.loop = asyncio.get_running_loop()

        def thread_safe_send(data):
            self.loop.call_soon_threadsafe(self.transport.write, data)

        def spawn_wsgi(target, args):
            return self.loop.run_in_executor(self.executor, target, *args)

        self.adapter = WSGIAdapter(self.wsgi_app, thread_safe_send, spawn_wsgi)

    def data_received(self, data):
        if self.adapter:
            self.adapter.handle_data(data)

    def eof_received(self):
        self.adapter = None
        return False

    def connection_lost(self, exc):
        self.adapter = None

async def run_wsgi_server(wsgi_app: Callable, 
                          bind_address: Union[str, Tuple[str, int], None] = None,
                          max_workers: int = None):
    """
    Run the FastCGI WSGI server using asyncio and a thread pool.
    """
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=max_workers)

    if bind_address is None:
        sock = socket.fromfd(FCGI_LISTENSOCK_FILENO, socket.AF_INET, socket.SOCK_STREAM)
        server = await loop.create_server(
            lambda: FastCGIWSGIProtocol(wsgi_app, executor),
            sock=sock
        )
    elif isinstance(bind_address, str):
        if os.path.exists(bind_address):
            os.unlink(bind_address)
        server = await loop.create_server(
            lambda: FastCGIWSGIProtocol(wsgi_app, executor),
            path=bind_address
        )
    else:
        server = await loop.create_server(
            lambda: FastCGIWSGIProtocol(wsgi_app, executor),
            host=bind_address[0],
            port=bind_address[1]
        )

    async with server:
        try:
            await server.serve_forever()
        finally:
            executor.shutdown()

if __name__ == "__main__":
    def simple_wsgi_app(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b"Hello from Asyncio-Threaded FastCGI WSGI!"]

    try:
        asyncio.run(run_wsgi_server(simple_wsgi_app, ("127.0.0.1", 9001)))
    except KeyboardInterrupt:
        pass
