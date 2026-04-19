import unittest
import struct
from fcgisgi.asgi_adapter import ASGIAdapter
from fcgisgi.wsgi_adapter import WSGIAdapter
from fcgisgi.sansio import (
    FCGI_VERSION_1,
    FCGI_GET_VALUES,
    FCGI_GET_VALUES_RESULT,
    FCGI_MAX_CONNS,
    FCGI_MAX_REQS,
    FCGI_MPXS_CONNS,
    FCGI_HEADER_FORMAT,
    FastCGIConnection,
)


class TestGetValues(unittest.TestCase):
    def test_asgi_get_values(self):
        async def app(scope, receive, send):
            pass

        output = bytearray()

        def send_func(data):
            output.extend(data)

        adapter = ASGIAdapter(app, send_func, on_close=lambda: None)

        fcgi = FastCGIConnection()
        # Request all known values
        keys = {FCGI_MAX_CONNS: b"", FCGI_MAX_REQS: b"", FCGI_MPXS_CONNS: b""}
        content = bytearray()
        for k, v in keys.items():
            content.extend(fcgi.encode_pair(k, v))

        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_GET_VALUES, 0, len(content), 0)
        adapter.handle_data(header + content)

        # Verify response: type should be FCGI_GET_VALUES_RESULT (10), request_id should be 0
        self.assertGreater(len(output), 0)
        _, resp_type, resp_req_id, _, _ = struct.unpack(FCGI_HEADER_FORMAT, output[:8])
        self.assertEqual(resp_type, FCGI_GET_VALUES_RESULT)
        self.assertEqual(resp_req_id, 0)
        self.assertIn(FCGI_MAX_CONNS, output)
        self.assertIn(FCGI_MAX_REQS, output)
        self.assertIn(FCGI_MPXS_CONNS, output)

    def test_wsgi_get_values(self):
        def app(environ, start_response):
            return []

        output = bytearray()

        def send_func(data):
            output.extend(data)

        adapter = WSGIAdapter(
            app, send_func, spawn_func=lambda f, a: None, call_soon_func=lambda f, *a: None, on_close=lambda: None
        )

        fcgi = FastCGIConnection()
        keys = {FCGI_MAX_CONNS: b"", FCGI_MAX_REQS: b"", FCGI_MPXS_CONNS: b""}
        content = bytearray()
        for k, v in keys.items():
            content.extend(fcgi.encode_pair(k, v))

        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_GET_VALUES, 0, len(content), 0)
        adapter.handle_data(header + content)

        self.assertGreater(len(output), 0)
        _, resp_type, resp_req_id, _, _ = struct.unpack(FCGI_HEADER_FORMAT, output[:8])
        self.assertEqual(resp_type, FCGI_GET_VALUES_RESULT)
        self.assertEqual(resp_req_id, 0)
        self.assertIn(FCGI_MAX_CONNS, output)
        self.assertIn(FCGI_MAX_REQS, output)
        self.assertIn(FCGI_MPXS_CONNS, output)


if __name__ == "__main__":
    unittest.main()
