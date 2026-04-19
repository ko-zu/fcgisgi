import unittest
import asyncio
import threading
from fcgisgi.asgi_adapter import ASGIAdapter
from fcgisgi.wsgi_adapter import WSGIAdapter
from fcgisgi.sansio import (
    FCGI_VERSION_1,
    FCGI_BEGIN_REQUEST,
    FCGI_PARAMS,
    FCGI_STDIN,
    FCGI_RESPONDER,
    FCGI_KEEP_CONN,
)
import struct


def make_header(type_, req_id, content_len, padding_len=0):
    return struct.pack("!BBHHBx", FCGI_VERSION_1, type_, req_id, content_len, padding_len)


def make_begin_request(req_id):
    body = struct.pack("!HB5x", FCGI_RESPONDER, FCGI_KEEP_CONN)
    return make_header(FCGI_BEGIN_REQUEST, req_id, len(body)) + body


def make_params(req_id, params):
    content = b""
    for k, v in params.items():
        k_bytes = k.encode("latin-1")
        v_bytes = v.encode("latin-1")
        k_len = len(k_bytes)
        v_len = len(v_bytes)
        if k_len < 128:
            content += struct.pack("!B", k_len)
        else:
            content += struct.pack("!L", k_len | 0x80000000)
        if v_len < 128:
            content += struct.pack("!B", v_len)
        else:
            content += struct.pack("!L", v_len | 0x80000000)
        content += k_bytes + v_bytes

    res = make_header(FCGI_PARAMS, req_id, len(content)) + content
    res += make_header(FCGI_PARAMS, req_id, 0)  # Empty params to end
    return res


class TestForceScriptName(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_force_script_name(self):
        recorded_scope = None

        async def app(scope, receive, send):
            nonlocal recorded_scope
            recorded_scope = scope
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
                        "body": b"ok",
                    }
                )

        adapter = ASGIAdapter(app, lambda d: None, on_close=lambda: None, force_script_name="/myapp")

        # Simulate request
        adapter.handle_data(make_begin_request(1))
        adapter.handle_data(
            make_params(
                1,
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/hello",
                    "SCRIPT_NAME": "/cgi-bin/index.fcgi",
                },
            )
        )
        adapter.handle_data(make_header(FCGI_STDIN, 1, 0))  # End of stdin

        # Give some time for the task to run
        await asyncio.sleep(0.1)

        self.assertIsNotNone(recorded_scope)
        self.assertEqual(recorded_scope["root_path"], "/myapp")
        self.assertEqual(recorded_scope["path"], "/myapp/hello")

    def test_wsgi_force_script_name(self):
        recorded_environ = None

        def app(environ, start_response):
            nonlocal recorded_environ
            recorded_environ = environ
            start_response("200 OK", [])
            return [b"ok"]

        adapter = WSGIAdapter(
            app,
            lambda d: None,
            spawn_func=lambda f, args: threading.Thread(target=f, args=args).start(),
            call_soon_func=lambda f, *args: f(*args),
            on_close=lambda: None,
            force_script_name="/myapp",
        )

        # Simulate request
        adapter.handle_data(make_begin_request(1))
        adapter.handle_data(
            make_params(
                1,
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/hello",
                    "SCRIPT_NAME": "/cgi-bin/index.fcgi",
                },
            )
        )
        adapter.handle_data(make_header(FCGI_STDIN, 1, 0))  # End of stdin

        # WSGI runs in a thread, might need a bit of wait or joining
        # But here it's simple enough that it should have run or we can wait
        import time

        start = time.time()
        while recorded_environ is None and time.time() - start < 1:
            time.sleep(0.01)

        self.assertIsNotNone(recorded_environ)
        self.assertEqual(recorded_environ["SCRIPT_NAME"], "/myapp")
        self.assertEqual(recorded_environ["PATH_INFO"], "/hello")


if __name__ == "__main__":
    unittest.main()
