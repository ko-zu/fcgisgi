import unittest
import asyncio
import os
import tempfile
import shutil
import struct
from fcgisgi.asyncio_server import Server
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN,
    FCGI_RESPONDER, FCGI_KEEP_CONN, FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)

class TestUnixSocket(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.socket_path = os.path.join(self.test_dir, "test.sock")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    async def test_unix_server_lifecycle(self):
        async def app(scope, receive, send):
            if scope['type'] == 'http':
                await send({
                    'type': 'http.response.start',
                    'status': 200,
                    'headers': [(b'content-type', b'text/plain')],
                })
                await send({
                    'type': 'http.response.body',
                    'body': b"unix-ok",
                })

        server = Server(app, is_asgi=True)
        
        # Run server in background
        server_task = asyncio.create_task(server.run(self.socket_path))
        
        # Wait for socket to be created
        for _ in range(50):
            if os.path.exists(self.socket_path):
                break
            await asyncio.sleep(0.1)
        else:
            self.fail("Unix socket was not created")

        # Connect as a client
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path), 
                timeout=2.0
            )
            
            # 1. Begin Request
            content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
            header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
            writer.write(header + content)
            
            # 2. Params (empty)
            header_params_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
            writer.write(header_params_eof)
            
            # 3. Stdin (empty)
            header_stdin_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0)
            writer.write(header_stdin_eof)
            await writer.drain()

            # Read response
            response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
            self.assertIn(b"unix-ok", response)
            
            writer.close()
            await writer.wait_closed()
        finally:
            server.stop()
            await server_task

if __name__ == "__main__":
    unittest.main()
