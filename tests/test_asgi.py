import unittest
import asyncio
import struct
from fcgisgi.sansio import (
    FCGI_VERSION_1,
    FCGI_BEGIN_REQUEST,
    FCGI_PARAMS,
    FCGI_HEADER_FORMAT,
    FCGI_BEGIN_REQUEST_BODY_FORMAT,
)
from fcgisgi.asgi_adapter import ASGIAdapter


class TestASGIAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.output = bytearray()

        def send_func(data):
            self.output.extend(data)

        self.send_func = send_func

    async def test_simple_asgi(self):
        async def app(scope, receive, send):
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Hello ASGI",
                    }
                )

        adapter = ASGIAdapter(app, self.send_func, on_close=lambda: None, startup_complete=True)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)

        # Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header + params_content + header_eof)

        # Give some time for the task to run
        await asyncio.sleep(0.1)

        self.assertIn(b"Status: 200", self.output)
        self.assertIn(b"content-type: text/plain", self.output)
        self.assertIn(b"Hello ASGI", self.output)

    async def test_asgi_error(self):
        async def app(scope, receive, send):
            if scope["type"] == "http":
                raise Exception("App crashed")

        adapter = ASGIAdapter(app, self.send_func, on_close=lambda: None, startup_complete=True)

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

        self.assertIn(b"Status: 500", self.output)
        self.assertIn(b"Internal Server Error", self.output)

    async def test_asgi_unsupported_type(self):
        error_caught = False

        async def app(scope, receive, send):
            nonlocal error_caught
            if scope["type"] == "http":
                try:
                    await send({"type": "http.unsupported.type"})
                except ValueError as e:
                    if "Unsupported ASGI message type" in str(e):
                        error_caught = True

        adapter = ASGIAdapter(app, self.send_func, on_close=lambda: None, startup_complete=True)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        adapter.handle_data(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))

        await asyncio.sleep(0.1)
        self.assertTrue(error_caught, "ValueError was not raised/caught for unsupported type")


if __name__ == "__main__":
    unittest.main()
