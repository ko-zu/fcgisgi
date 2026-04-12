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

        adapter = ASGIAdapter(app, lambda d: None, on_close=lambda: None, startup_complete=True)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)

        # Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header + params_content + header_eof)

        await asyncio.sleep(0.1)

        # Simulate abort
        from fcgisgi.sansio import FCGI_ABORT_REQUEST
        header_abort = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_ABORT_REQUEST, 1, 0, 0)
        adapter.handle_data(header_abort)

        await asyncio.sleep(0.1)

        self.assertTrue(disconnected or task_cancelled,
                        "ASGI app did not receive disconnect or wasn't cancelled")

    async def test_send_after_disconnect(self):
        from fcgisgi.asgi_adapter import DisconnectedError

        async def app(scope, receive, send):
            if scope['type'] == 'http':
                try:
                    while True:
                        msg = await receive()
                        if msg['type'] == 'http.disconnect':
                            break
                finally:
                    # Try to send something after disconnect/abort
                    try:
                        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                    except DisconnectedError:
                        app.error_raised = True

        app.error_raised = False
        adapter = ASGIAdapter(app, lambda d: None, on_close=lambda: None, startup_complete=True)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        adapter.handle_data(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))

        await asyncio.sleep(0.1)

        # Simulate abort
        from fcgisgi.sansio import FCGI_ABORT_REQUEST
        adapter.handle_data(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_ABORT_REQUEST, 1, 0, 0))

        await asyncio.sleep(0.1)
        self.assertTrue(app.error_raised, "DisconnectedError was not raised after abort")

    async def test_send_after_completion(self):
        from fcgisgi.asgi_adapter import DisconnectedError

        async def app(scope, receive, send):
            if scope['type'] == 'http':
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b'ok', 'more_body': False})

                # Try to send more after more_body=False
                try:
                    await send({'type': 'http.response.body', 'body': b'extra'})
                except DisconnectedError:
                    app.error_raised = True

        app.error_raised = False
        adapter = ASGIAdapter(app, lambda d: None, on_close=lambda: None, startup_complete=True)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        adapter.handle_data(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))

        await asyncio.sleep(0.1)
        self.assertTrue(app.error_raised, "DisconnectedError was not raised after response completion")


if __name__ == "__main__":
    unittest.main()
