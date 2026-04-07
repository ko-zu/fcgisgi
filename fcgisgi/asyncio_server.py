import asyncio
import signal
import socket
import os
import logging
from typing import Callable, Union, Tuple, Optional, Any

from .sansio import FCGI_LISTENSOCK_FILENO
from .asgi_adapter import ASGIAdapter
from .wsgi_adapter import WSGIAdapter

logger = logging.getLogger(__name__)


class FastCGIASGIProtocol(asyncio.Protocol):
    """ASGI specific protocol implementation."""

    def __init__(self, app: Callable, server: 'Server'):
        self.app = app
        self.server = server
        self.adapter = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.server.protocols.add(self)
        self.adapter = ASGIAdapter(
            self.app,
            self.transport.write,
            startup_complete=self.server.startup_complete,
            force_script_name=self.server.force_script_name
        )

    def data_received(self, data):
        if self.adapter:
            self.adapter.handle_data(data)

    def eof_received(self):
        if self.adapter:
            self.adapter.close_all()
            self.adapter = None
        return False

    def connection_lost(self, exc):
        self.server.protocols.discard(self)
        if self.adapter:
            self.adapter.close_all()
            self.adapter = None


class FastCGIWSGIProtocol(asyncio.Protocol):
    """WSGI specific protocol implementation."""

    def __init__(self, app: Callable, executor: Any, server: 'Server'):
        self.app = app
        self.executor = executor
        self.server = server
        self.adapter = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.server.protocols.add(self)
        loop = asyncio.get_running_loop()

        # Connection-specific thread-safe send function
        def thread_safe_send(d):
            loop.call_soon_threadsafe(self.transport.write, d)

        # Create a new adapter instance per connection
        self.adapter = WSGIAdapter(
            self.app,
            thread_safe_send,
            lambda target, args: loop.run_in_executor(
                self.executor, target, *args),
            loop.call_soon_threadsafe,
            force_script_name=self.server.force_script_name
        )

    def data_received(self, data):
        if self.adapter:
            self.adapter.handle_data(data)

    def eof_received(self):
        if self.adapter:
            self.adapter.close_all()
            self.adapter = None
        return False

    def connection_lost(self, exc):
        self.server.protocols.discard(self)
        if self.adapter:
            self.adapter.close_all()
            self.adapter = None


class Server:
    def __init__(self, app: Callable, is_asgi: bool = True, force_script_name: Optional[str] = None, **kwargs):
        self.app = app
        self.is_asgi = is_asgi
        self.force_script_name = force_script_name
        self.kwargs = kwargs
        self.startup_complete = not is_asgi
        self._stop_event = None
        self._lifespan_task = None
        self._lifespan_queue = None
        self._startup_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self.protocols = set()

    async def run(self, bind_address: Union[str, Tuple[str, int], None] = None):
        self.loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        # 1. Start Lifespan for ASGI
        if self.is_asgi:
            self._lifespan_queue = asyncio.Queue()
            self._lifespan_task = asyncio.create_task(self._run_lifespan())
            await self._lifespan_queue.put({"type": "lifespan.startup"})
            try:
                timeout = self.kwargs.get('startup_timeout', 10.0)
                await asyncio.wait_for(self._startup_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.error("ASGI Lifespan startup timed out")
            self.startup_complete = True

        # 2. Setup Signals
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, self.stop)

        # 3. Setup Socket and Server
        executor = None
        if not self.is_asgi:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=self.kwargs.get('max_workers'),
                                          thread_name_prefix="wsgi-worker")

        def protocol_factory():
            if self.is_asgi:
                return FastCGIASGIProtocol(self.app, self)
            else:
                return FastCGIWSGIProtocol(self.app, executor, self)

        if bind_address is None:
            # Use socket.fromfd to probe the family without closing the original fd=0.
            try:
                probe_sock = socket.fromfd(FCGI_LISTENSOCK_FILENO,
                                           socket.AF_INET, socket.SOCK_STREAM)
                try:
                    # 39 is SO_DOMAIN on Linux.
                    family = probe_sock.getsockopt(
                        socket.SOL_SOCKET, getattr(socket, 'SO_DOMAIN', 39))
                except (AttributeError, OSError):
                    family = socket.AF_INET
                probe_sock.close()
            except:
                family = socket.AF_INET

            if family == socket.AF_UNIX:
                sock = socket.fromfd(FCGI_LISTENSOCK_FILENO,
                                     socket.AF_UNIX, socket.SOCK_STREAM)
                server = await self.loop.create_unix_server(protocol_factory, sock=sock)
            else:
                sock = socket.fromfd(FCGI_LISTENSOCK_FILENO,
                                     family, socket.SOCK_STREAM)
                server = await self.loop.create_server(protocol_factory, sock=sock)
        elif isinstance(bind_address, str):
            if os.path.exists(bind_address):
                import stat
                try:
                    st = os.stat(bind_address)
                    if stat.S_ISSOCK(st.st_mode):
                        os.unlink(bind_address)
                except OSError:
                    pass
            server = await self.loop.create_unix_server(protocol_factory, path=bind_address)
        else:
            server = await self.loop.create_server(protocol_factory, host=bind_address[0], port=bind_address[1])

        try:
            async with server:
                await self._stop_event.wait()
        finally:
            # 4. Graceful Shutdown
            # First, signal all active protocols to abort their tasks
            for proto in list(self.protocols):
                if proto.adapter:
                    proto.adapter.close_all()

            server.close()
            await server.wait_closed()

            shutdown_timeout = self.kwargs.get('shutdown_timeout', 10.0)
            if self.is_asgi:
                await self._lifespan_queue.put({"type": "lifespan.shutdown"})
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=shutdown_timeout)
                except asyncio.TimeoutError:
                    logger.error("ASGI Lifespan shutdown timed out")
                if self._lifespan_task:
                    self._lifespan_task.cancel()
            elif executor:
                try:
                    await asyncio.wait_for(asyncio.to_thread(executor.shutdown), timeout=shutdown_timeout)
                except asyncio.TimeoutError:
                    logger.error("WSGI thread pool shutdown timed out")

    async def _run_lifespan(self):
        scope = {"type": "lifespan", "asgi": {
            "version": "3.0", "spec_version": "2.0"}}

        async def receive():
            try:
                return await self._lifespan_queue.get()
            except asyncio.CancelledError:
                return {"type": "lifespan.shutdown"}

        async def send(message):
            if message["type"] == "lifespan.startup.complete":
                self._startup_event.set()
            elif message["type"] == "lifespan.shutdown.complete":
                self._shutdown_event.set()
            elif message["type"] in ("lifespan.startup.failed", "lifespan.shutdown.failed"):
                self._startup_event.set()
                self._shutdown_event.set()
        try:
            await self.app(scope, receive, send)
        except Exception:
            pass
        finally:
            self._startup_event.set()
            self._shutdown_event.set()

    def stop(self):
        if self._stop_event:
            self._stop_event.set()


async def run_asgi_server(app: Callable, bind_address=None, force_script_name: Optional[str] = None, **kwargs):
    server = Server(app, is_asgi=True,
                    force_script_name=force_script_name, **kwargs)
    await server.run(bind_address)


async def run_wsgi_server(app: Callable, bind_address=None, force_script_name: Optional[str] = None, **kwargs):
    server = Server(app, is_asgi=False,
                    force_script_name=force_script_name, **kwargs)
    await server.run(bind_address)
