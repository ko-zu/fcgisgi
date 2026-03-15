import unittest
import asyncio
import struct
from unittest.mock import MagicMock
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.asyncio_server import FastCGIProtocol
from fcgisgi.asgi_adapter import ASGIAdapter

class TestFastCGIProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_interaction(self):
        # A simple ASGI app that returns OK
        async def app(scope, receive, send):
            if scope['type'] == 'http':
                await send({
                    'type': 'http.response.start',
                    'status': 200,
                    'headers': [],
                })
                await send({
                    'type': 'http.response.body',
                    'body': b'OK',
                })

        # Mock transport to capture output
        transport = MagicMock()
        output = bytearray()
        transport.write.side_effect = lambda data: output.extend(data)

        adapter = ASGIAdapter(app)
        await adapter.startup()
        protocol = FastCGIProtocol(adapter)
        protocol.connection_made(transport)

        # Simulate inbound FastCGI data
        # 1. Begin Request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        protocol.data_received(header + content)

        # 2. Params
        params_content = b"\x0b\x01SCRIPT_NAME\x00"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, len(params_content), 0)
        header_eof = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        protocol.data_received(header + params_content + header_eof)

        # Give some time for the ASGI app task to run
        await asyncio.sleep(0.1)

        # Verify that data was written to the transport
        self.assertIn(b"Status: 200", output)
        self.assertIn(b"OK", output)
        await adapter.shutdown()

if __name__ == "__main__":
    unittest.main()
