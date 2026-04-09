import unittest
import asyncio
import struct
import threading
from unittest.mock import MagicMock
from fcgisgi.asgi_adapter import ASGIAdapter
from fcgisgi.wsgi_adapter import WSGIAdapter
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN,
    FCGI_RESPONDER, FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)


class TestKeepConn(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_keep_conn_false_closes(self):
        on_close_called = asyncio.Event()

        def on_close():
            on_close_called.set()

        async def app(scope, receive, send):
            if scope['type'] == 'http':
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b"ok"})

        adapter = ASGIAdapter(app, lambda d: None, on_close=on_close)

        # flags = 0 (KEEP_CONN is not set)
        content = struct.pack(
            FCGI_BEGIN_REQUEST_BODY_FORMAT, FCGI_RESPONDER, 0)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)

        # Params EOF
        header_params_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header_params_eof)

        # Wait for app and check if on_close was called
        await asyncio.wait_for(on_close_called.wait(), timeout=1.0)
        self.assertTrue(on_close_called.is_set())

    async def test_wsgi_keep_conn_false_closes(self):
        on_close_called = threading.Event()

        def on_close():
            on_close_called.set()

        def app(environ, start_response):
            start_response('200 OK', [])
            return [b"ok"]

        # Basic mocks for WSGIAdapter requirements
        def spawn_func(f, args):
            t = threading.Thread(target=f, args=args)
            t.start()
            return t

        adapter = WSGIAdapter(app, lambda d: None,
                              spawn_func=spawn_func,
                              call_soon_func=lambda f, *args: f(*args),
                              on_close=on_close)

        # flags = 0 (KEEP_CONN is not set)
        content = struct.pack(
            FCGI_BEGIN_REQUEST_BODY_FORMAT, FCGI_RESPONDER, 0)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)

        # Params EOF
        header_params_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header_params_eof)

        # Wait for thread and check if on_close was called
        # Use a loop to avoid blocking indefinitely if it fails
        import time
        start = time.time()
        while not on_close_called.is_set() and time.time() - start < 2.0:
            await asyncio.sleep(0.1)

        self.assertTrue(on_close_called.is_set())

    async def test_asgi_keep_conn_true_does_not_close(self):
        on_close_called = asyncio.Event()

        def on_close():
            on_close_called.set()

        async def app(scope, receive, send):
            if scope['type'] == 'http':
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b"ok"})

        from fcgisgi.sansio import FCGI_KEEP_CONN
        adapter = ASGIAdapter(app, lambda d: None, on_close=on_close)

        # flags = FCGI_KEEP_CONN
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT,
                              FCGI_RESPONDER, FCGI_KEEP_CONN)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        adapter.handle_data(struct.pack(FCGI_HEADER_FORMAT,
                            FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))

        # Wait a bit to ensure it WOULD have been called if it were going to be
        await asyncio.sleep(0.5)
        self.assertFalse(on_close_called.is_set())


if __name__ == "__main__":
    unittest.main()
