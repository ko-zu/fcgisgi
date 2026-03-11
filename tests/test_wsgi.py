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
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        
        # Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00" # Just one param for now
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
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

if __name__ == "__main__":
    unittest.main()
