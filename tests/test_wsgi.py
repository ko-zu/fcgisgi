import unittest
import struct
import io
import time
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT, FastCGIConnection
)
from fcgisgi.wsgi_adapter import WSGIAdapter


class TestWSGIAdapter(unittest.TestCase):
    def setUp(self):
        self.output = bytearray()

        def send_func(data):
            self.output.extend(data)
        self.send_func = send_func

    def test_simple_wsgi(self):
        def app(environ, start_response):
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return [b"Hello World"]

        adapter = WSGIAdapter(app, self.send_func)

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)

        # Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"  # Just one param for now
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        adapter.handle_data(header + params_content + header_eof)

        # Wait for thread to finish
        time.sleep(0.1)

        # Check output
        # Output should contain STDOUT (headers + body) and END_REQUEST
        # We can use FastCGIConnection to parse the output
        conn = FastCGIConnection()
        events = conn.feed_data(self.output)

        # We don't have events for STDOUT in our FastCGIConnection yet (it only parses inbound)
        # But we can check the raw output or update FastCGIConnection to handle all types
        self.assertIn(b"Status: 200 OK", self.output)
        self.assertIn(b"Content-Type: text/plain", self.output)
        self.assertIn(b"Hello World", self.output)

    def test_wsgi_error(self):
        import logging
        logging.getLogger('fcgisgi.wsgi_adapter').setLevel(logging.CRITICAL)
        try:
            def app(environ, start_response):
                raise Exception("App crashed")

            adapter = WSGIAdapter(app, self.send_func)

            # Start request
            content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
            header = struct.pack(
                FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
            adapter.handle_data(header + content)

            # Params
            params_content = b"\x0b\x01SCRIPT_NAME\x00"
            header = struct.pack(
                FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
            header_eof = struct.pack(
                FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
            adapter.handle_data(header + params_content + header_eof)

            time.sleep(0.1)

            self.assertIn(b"Status: 500 Internal Server Error", self.output)
            self.assertIn(b"Internal Server Error", self.output)
        finally:
            logging.getLogger('fcgisgi.wsgi_adapter').setLevel(logging.NOTSET)

    def test_wsgi_errors(self):
        def app(environ, start_response):
            environ['wsgi.errors'].write("Error: こんにちは\n")
            start_response('200 OK', [])
            return [b"OK"]

        adapter = WSGIAdapter(app, self.send_func)

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

        time.sleep(0.1)

        # Check if FCGI_STDERR was sent
        from fcgisgi.sansio import FCGI_STDERR
        # FastCGI header for STDERR: type=7, requestId=1
        expected_header = struct.pack(
            FCGI_HEADER_FORMAT, 1, FCGI_STDERR, 1, 0, 0)[:3]
        self.assertIn(expected_header, self.output)
        # Check for encoded content
        self.assertIn("こんにちは".encode('utf-8'), self.output)


if __name__ == "__main__":
    unittest.main()
