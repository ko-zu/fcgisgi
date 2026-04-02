import unittest
import asyncio
import struct
import time
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.asyncio_server import FastCGIWSGIProtocol, Server
from fcgisgi.wsgi_adapter import WSGIAdapter

class TestFastCGIWSGIProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_multiconnection_isolation(self):
        """
        Verify that multiple concurrent connections to the same app
        correctly route their responses to their respective transports.
        """
        def app(environ, start_response):
            # Echo back a custom header sent in the request to verify isolation
            conn_id = environ.get('HTTP_X_CONN_ID', 'unknown')
            start_response('200 OK', [('X-Conn-Response', conn_id)])
            return [f"Response for {conn_id}".encode('latin-1')]

        executor = ThreadPoolExecutor(max_workers=4)
        server = Server(app, is_asgi=False)
        server.loop = asyncio.get_running_loop()

        # Setup Connection 1
        transport1 = MagicMock()
        output1 = bytearray()
        transport1.write.side_effect = lambda d: output1.extend(d)
        proto1 = FastCGIWSGIProtocol(app, executor, server)
        proto1.connection_made(transport1)

        # Setup Connection 2
        transport2 = MagicMock()
        output2 = bytearray()
        transport2.write.side_effect = lambda d: output2.extend(d)
        proto2 = FastCGIWSGIProtocol(app, executor, server)
        proto2.connection_made(transport2)

        def send_request(proto, conn_id, request_id):
            # 1. Begin Request
            content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
            header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, request_id, len(content), 0)
            proto.data_received(header + content)

            # 2. Params with Conn-ID using proper encoding
            from fcgisgi.sansio import FastCGIConnection
            fcgi = FastCGIConnection()
            params_data = fcgi.encode_pair(b"HTTP_X_CONN_ID", conn_id.encode('latin-1'))
            header_params = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, request_id, len(params_data), 0)
            header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, request_id, 0, 0)
            proto.data_received(header_params + params_data + header_eof)

        # Send requests concurrently
        send_request(proto1, "CONN1", 1)
        send_request(proto2, "CONN2", 1)

        # Wait for threads
        await asyncio.sleep(0.3)

        # Verify Isolation
        self.assertIn(b"X-Conn-Response: CONN1", output1)
        self.assertIn(b"Response for CONN1", output1)
        self.assertNotIn(b"CONN2", output1)

        self.assertIn(b"X-Conn-Response: CONN2", output2)
        self.assertIn(b"Response for CONN2", output2)
        self.assertNotIn(b"CONN1", output2)

        executor.shutdown()

if __name__ == "__main__":
    unittest.main()
