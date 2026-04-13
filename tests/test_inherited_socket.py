import unittest
import asyncio
import socket
import os
import struct
import tempfile
from unittest.mock import patch, MagicMock
from fcgisgi.asyncio_server import Server
from fcgisgi.sansio import (
    FCGI_LISTENSOCK_FILENO,
    FCGI_VERSION_1,
    FCGI_BEGIN_REQUEST,
    FCGI_PARAMS,
    FCGI_STDIN,
    FCGI_RESPONDER,
    FCGI_KEEP_CONN,
    FCGI_HEADER_FORMAT,
    FCGI_BEGIN_REQUEST_BODY_FORMAT,
)


class TestInheritedSocket(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = self._temp_dir.name

    def tearDown(self):
        self._temp_dir.cleanup()

    async def run_inherited_test(self, listener_sock, family):
        async def app(scope, receive, send):
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"inherited-ok",
                    }
                )

        # We need to mock socket.fromfd so that when the server tries to
        # "inherit" fd=0, it actually gets our listener_sock (duplicated).

        original_fromfd = socket.fromfd

        def mocked_fromfd(fd, fam, typ):
            if fd == FCGI_LISTENSOCK_FILENO:
                # Return a NEW socket object that refers to the same underlying
                # socket as our listener_sock, but with the requested family.
                return socket.fromfd(listener_sock.fileno(), fam, typ)
            return original_fromfd(fd, fam, typ)

        server = Server(app)

        with patch("socket.fromfd", side_effect=mocked_fromfd):
            # Also need to mock the probe_sock.getsockopt if SO_DOMAIN is not available
            # or to ensure it returns the correct family for our test.

            # Since our implementation uses socket.fromfd(0, AF_UNIX, ...) to probe,
            # and our mocked_fromfd will return a socket from listener_sock.fileno(),
            # getsockopt(SO_DOMAIN) should naturally return the correct family
            # if the OS supports it. If not, we might need to mock getsockopt too.

            server_task = asyncio.create_task(server.run(None))
            await asyncio.sleep(0.2)  # Wait for server to start

            try:
                if family == socket.AF_INET:
                    host, port = listener_sock.getsockname()
                    reader, writer = await asyncio.open_connection(host, port)
                else:
                    path = listener_sock.getsockname()
                    reader, writer = await asyncio.open_unix_connection(path)

                # Send Minimal FastCGI request
                content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
                header = struct.pack(
                    FCGI_HEADER_FORMAT,
                    FCGI_VERSION_1,
                    FCGI_BEGIN_REQUEST,
                    1,
                    len(content),
                    0,
                )
                writer.write(header + content)
                writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))
                writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0))
                await writer.drain()

                response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                self.assertIn(b"inherited-ok", response)

                writer.close()
                await writer.wait_closed()
            finally:
                server.stop()
                await server_task

    async def test_inherited_tcp_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            await self.run_inherited_test(listener, socket.AF_INET)
        finally:
            listener.close()

    async def test_inherited_unix_socket(self):
        socket_path = os.path.join(self.test_dir, "inherited.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(socket_path)
        listener.listen(1)
        try:
            await self.run_inherited_test(listener, socket.AF_UNIX)
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
