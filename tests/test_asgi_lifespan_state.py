import unittest
import asyncio
import struct
from fcgisgi.asyncio_server import Server
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN,
    FCGI_RESPONDER, FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)

class TestASGILifespanState(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_state_propagation(self):
        startup_called = asyncio.Event()
        shutdown_called = asyncio.Event()
        recorded_request_state = None
        recorded_shutdown_state = None

        async def app(scope, receive, send):
            if scope['type'] == 'lifespan':
                while True:
                    message = await receive()
                    if message['type'] == 'lifespan.startup':
                        # 1. Set value in state during startup
                        scope['state']['app_val'] = 'initialized'
                        await send({'type': 'lifespan.startup.complete'})
                        startup_called.set()
                    elif message['type'] == 'lifespan.shutdown':
                        # 3. Check value in state during shutdown
                        nonlocal recorded_shutdown_state
                        recorded_shutdown_state = scope.get('state', {}).copy()
                        await send({'type': 'lifespan.shutdown.complete'})
                        shutdown_called.set()
                        return
            elif scope['type'] == 'http':
                # 2. Check value in state during request
                nonlocal recorded_request_state
                recorded_request_state = scope['state'].copy()
                del scope['state']['app_val']
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'ok'})

        server = Server(app, is_asgi=True)

        # Use a temporary file for UNIX socket
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = os.path.join(tmpdir, "test.sock")

            # Start server
            server_task = asyncio.create_task(server.run(sock_path))

            # Wait for lifespan startup to finish
            await asyncio.wait_for(startup_called.wait(), timeout=2.0)

            # Connect and send a request
            reader, writer = await asyncio.open_unix_connection(sock_path)

            content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, FCGI_RESPONDER, 0)
            header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
            writer.write(header + content)
            writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))
            writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0))
            await writer.drain()

            await reader.read(1024)
            writer.close()
            await writer.wait_closed()

            # Stop server to trigger lifespan shutdown
            server.stop()
            await asyncio.wait_for(shutdown_called.wait(), timeout=2.0)
            await server_task

        # Verify results
        self.assertIsNotNone(recorded_request_state)
        self.assertEqual(recorded_request_state.get('app_val'), 'initialized')

        self.assertIsNotNone(recorded_shutdown_state)
        self.assertEqual(recorded_shutdown_state.get('app_val'), 'initialized')

if __name__ == "__main__":
    unittest.main()
