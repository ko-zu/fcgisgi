import asyncio
import sys
import os
import socket
from typing import Callable, Union, Tuple, Optional

from .sansio import FCGI_LISTENSOCK_FILENO
from .asgi_adapter import ASGIAdapter

class FastCGIProtocol(asyncio.Protocol):
    def __init__(self, adapter: ASGIAdapter):
        self.adapter = adapter
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        # We need a way for the adapter to send data back to this specific transport.
        # However, ASGIAdapter currently takes a single send_func.
        # To support multiple concurrent connections, we need to route send calls.
        
        # We'll monkey-patch a per-connection adapter or use a proxy.
        # For simplicity in this implementation, we'll create a local send_func
        # and tell the adapter about it for this connection.
        # But Wait! The adapter already handles multiple request_ids.
        # The only thing it needs is a way to map request_id -> transport.
        pass

    def data_received(self, data):
        # We need to tell the adapter which transport to use for responses to this data
        # Let's wrap the adapter's send_func or similar.
        self.adapter.handle_data(data, self.transport.write)

    def eof_received(self):
        if self.adapter:
            # We don't want to close ALL requests, just the ones for this connection
            # But FastCGI connections usually map 1:1 to a socket.
            # So close_all for this connection is correct.
            self.adapter.close_connection(self.transport.write)
        return False

    def connection_lost(self, exc):
        if self.adapter:
            self.adapter.close_connection(self.transport.write)

async def run_server(asgi_app: Callable, bind_address: Union[str, Tuple[str, int], None] = None):
    """
    Run the FastCGI server using asyncio with Lifespan support.
    """
    loop = asyncio.get_running_loop()

    # Shared adapter for all connections to share the same Lifespan
    # We need to update ASGIAdapter to handle multiple transports
    adapter = ASGIAdapter(asgi_app, None) 
    
    await adapter.startup()

    def protocol_factory():
        return FastCGIProtocol(adapter)

    if bind_address is None:
        sock = socket.fromfd(FCGI_LISTENSOCK_FILENO, socket.AF_INET, socket.SOCK_STREAM)
        server = await loop.create_server(protocol_factory, sock=sock)
    elif isinstance(bind_address, str):
        if os.path.exists(bind_address):
            os.unlink(bind_address)
        server = await loop.create_server(protocol_factory, path=bind_address)
    else:
        server = await loop.create_server(protocol_factory, host=bind_address[0], port=bind_address[1])

    async with server:
        try:
            await server.serve_forever()
        finally:
            await adapter.shutdown()
