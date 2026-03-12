import asyncio
import sys
import os
import socket
from typing import Callable, Union, Tuple

from .sansio import FCGI_LISTENSOCK_FILENO
from .asgi_adapter import ASGIAdapter

class FastCGIProtocol(asyncio.Protocol):
    def __init__(self, asgi_app: Callable):
        self.asgi_app = asgi_app
        self.adapter = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        # The adapter needs a way to send data back to the socket
        self.adapter = ASGIAdapter(self.asgi_app, self.transport.write)

    def data_received(self, data):
        if self.adapter:
            self.adapter.handle_data(data)

    def eof_received(self):
        # We received EOF from the other end. Returning False will close the transport.
        self.adapter = None
        return False

    def connection_lost(self, exc):
        self.adapter = None

async def run_server(asgi_app: Callable, bind_address: Union[str, Tuple[str, int], None] = None):
    """
    Run the FastCGI server using asyncio.
    
    If bind_address is None, it attempts to use the socket inherited from the web server (fd 0).
    If bind_address is a string, it binds to a UNIX socket.
    If bind_address is a tuple (host, port), it binds to a TCP socket.
    """
    loop = asyncio.get_running_loop()

    if bind_address is None:
        # FastCGI convention: the listening socket is passed on file descriptor 0
        try:
            sock = socket.fromfd(FCGI_LISTENSOCK_FILENO, socket.AF_INET, socket.SOCK_STREAM)
            # Check if it's actually a socket
            sock.getpeername()
        except OSError:
            # If getpeername fails with ENOTCONN, it's a listening socket.
            # If it fails with ENOTSOCK, it's not a socket at all.
            pass
        
        # In many FastCGI setups, fd 0 is the socket we should listen on.
        # We need to create a server from this existing socket.
        server = await loop.create_server(
            lambda: FastCGIProtocol(asgi_app),
            sock=sock
        )
    elif isinstance(bind_address, str):
        # UNIX socket
        if os.path.exists(bind_address):
            os.unlink(bind_address)
        server = await loop.create_server(
            lambda: FastCGIProtocol(asgi_app),
            path=bind_address
        )
    else:
        # TCP socket
        server = await loop.create_server(
            lambda: FastCGIProtocol(asgi_app),
            host=bind_address[0],
            port=bind_address[1]
        )

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    # Example usage:
    async def simple_app(scope, receive, send):
        if scope['type'] == 'http':
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'text/plain')],
            })
            await send({
                'type': 'http.response.body',
                'body': b'Hello from Asyncio FastCGI!',
            })

    # Run on a local port for testing
    try:
        asyncio.run(run_server(simple_app, ("127.0.0.1", 9000)))
    except KeyboardInterrupt:
        pass
