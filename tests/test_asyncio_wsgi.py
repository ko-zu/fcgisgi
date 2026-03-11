import unittest
import asyncio
import struct
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.asyncio_wsgi_server import FastCGIWSGIProtocol

class TestFastCGIWSGIProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_interaction(self):
        def app(environ, start_response):
            start_response('200 OK', [])
            return [b"WSGI OK"]

        transport = MagicMock()
        output = bytearray()
        transport.write.side_effect = lambda data: output.extend(data)

        executor = ThreadPoolExecutor(max_workers=1)
        protocol = FastCGIWSGIProtocol(app, executor)
        protocol.connection_made(transport)

        # 1. Begin Request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        protocol.data_received(header + content)

        # 2. Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        protocol.data_received(header + params_content + header_eof)

        # Give time for thread pool and call_soon_threadsafe
        await asyncio.sleep(0.2)

        self.assertIn(b"Status: 200", output)
        self.assertIn(b"WSGI OK", output)
        
        executor.shutdown()

if __name__ == "__main__":
    unittest.main()
