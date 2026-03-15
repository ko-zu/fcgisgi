import unittest
import asyncio
import struct
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.asgi_adapter import ASGIAdapter

class TestASGIDisconnect(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_disconnect_on_abort(self):
        disconnected = False
        task_cancelled = False

        async def app(scope, receive, send):
            nonlocal disconnected, task_cancelled
            if scope['type'] == 'lifespan':
                while True:
                    msg = await receive()
                    if msg['type'] == 'lifespan.startup':
                        await send({'type': 'lifespan.startup.complete'})
                    elif msg['type'] == 'lifespan.shutdown':
                        await send({'type': 'lifespan.shutdown.complete'})
                        return
            
            try:
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        disconnected = True
                        break
            except asyncio.CancelledError:
                task_cancelled = True
                raise

        adapter = ASGIAdapter(app, lambda d: None)
        await adapter.startup()
        
        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        
        # Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header + params_content + header_eof)
        
        await asyncio.sleep(0.1)
        
        # Simulate abort
        from fcgisgi.sansio import FCGI_ABORT_REQUEST
        header_abort = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_ABORT_REQUEST, 1, 0, 0)
        adapter.handle_data(header_abort)
        
        await asyncio.sleep(0.1)
        
        self.assertTrue(disconnected or task_cancelled, "ASGI app did not receive disconnect or wasn't cancelled")
        await adapter.shutdown()

if __name__ == "__main__":
    unittest.main()
