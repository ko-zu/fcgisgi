import unittest
import asyncio
import struct
from unittest.mock import MagicMock
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.asyncio_server import FastCGIASGIProtocol, Server


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

        server = Server(app)
        server.startup_complete = True
        protocol = FastCGIASGIProtocol(app, server)
        protocol.connection_made(transport)

        # Simulate inbound FastCGI data
        # 1. Begin Request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        protocol.data_received(header + content)

        # 2. Params
        from fcgisgi.sansio import FastCGIConnection
        fcgi = FastCGIConnection()
        params_data = fcgi.encode_pair(b"SCRIPT_NAME", b"")
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_PARAMS, 1, len(params_data), 0)
        header_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)
        protocol.data_received(header + params_data + header_eof)

        # Give some time for the ASGI app task to run
        await asyncio.sleep(0.1)

        # Verify that data was written to the transport
        self.assertIn(b"Status: 200", output)
        self.assertIn(b"OK", output)


class TestServerProcess(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_server_subprocess(self):
        import sys
        import os
        import tempfile
        import signal
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = os.path.join(tmpdir, "test.sock")
            app_path = os.path.join(tmpdir, "app.py")

            # Create a minimal server script
            with open(app_path, "w") as f:
                f.write(f"""
import asyncio
import logging
import sys
from fcgisgi import run_asgi_server

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("test-app")

async def app(scope, receive, send):
    if scope['type'] == 'http':
        await send({{'type': 'http.response.start', 'status': 200, 'headers': []}})
        await send({{'type': 'http.response.body', 'body': b'process-ok'}})

if __name__ == "__main__":
    logger.info("Starting server")
    try:
        asyncio.run(run_asgi_server(app, bind_address={repr(sock_path)}))
    finally:
        logger.info("Server exited")
""")

            # Launch the server process
            env = os.environ.copy()
            env["PYTHONPATH"] = os.getcwd()
            proc = subprocess.Popen(
                [sys.executable, app_path],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Wait for socket
            start = time.time()
            while not os.path.exists(sock_path) and time.time() - start < 5.0:
                await asyncio.sleep(0.1)
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate()
                    self.fail(f"Server process exited prematurely with code {proc.returncode}\nStderr: {stderr.decode()}")

            self.assertTrue(os.path.exists(sock_path))

            # Connect and verify
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(sock_path),
                    timeout=2.0
                )

                # Simple FCGI request
                content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
                header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
                writer.write(header + content + struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0) + struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0))
                await writer.drain()

                response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                self.assertIn(b"process-ok", response)

                writer.close()
                await writer.wait_closed()
            finally:
                # Graceful shutdown
                proc.send_signal(signal.SIGTERM)

                # Wait for process to exit
                try:
                    # Python 3.9+ supports timeout in communicate
                    stdout, stderr = proc.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    self.fail(f"Server process did not exit gracefully on SIGTERM\nStderr: {stderr.decode()}")

                self.assertEqual(proc.returncode, 0)

    async def test_asgi_server_fd_inheritance(self):
        import sys
        import os
        import tempfile
        import signal
        import subprocess
        import socket
        import time

        # Create a listener socket
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener_fd = listener.fileno()
        host, port = listener.getsockname()

        with tempfile.TemporaryDirectory() as tmpdir:
            app_path = os.path.join(tmpdir, "app_fd.py")
            wrapper_path = os.path.join(tmpdir, "wrapper.py")

            # Create a server script
            with open(app_path, "w") as f:
                f.write(f"""
import asyncio
from fcgisgi import run_asgi_server

async def app(scope, receive, send):
    if scope['type'] == 'http':
        await send({{'type': 'http.response.start', 'status': 200, 'headers': []}})
        await send({{'type': 'http.response.body', 'body': b'fd-inheritance-ok'}})

if __name__ == "__main__":
    asyncio.run(run_asgi_server(app))
""")

            # Create a wrapper script that handles FD mapping and signal propagation via exec
            with open(wrapper_path, "w") as f:
                f.write(f"""
import os
import sys

# Map inherited FD to 0
os.dup2({listener_fd}, 0)
os.close({listener_fd})

# Use exec to replace the current process with the server,
# ensuring signals (SIGTERM) are handled by the server itself.
os.environ["PYTHONPATH"] = {repr(os.getcwd())}
os.execv(sys.executable, [sys.executable, {repr(app_path)}])
""")

            # Launch the wrapper process
            proc = subprocess.Popen(
                [sys.executable, wrapper_path],
                pass_fds=[listener_fd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Connect and verify
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=5.0
                )

                # Simple FCGI request
                content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
                header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
                writer.write(header + content)
                writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0))
                writer.write(struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0))
                await writer.drain()

                response = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                self.assertIn(b"fd-inheritance-ok", response)

                writer.close()
                await writer.wait_closed()
            finally:
                # Signals will be sent directly to the server process due to os.execv
                proc.terminate()
                try:
                    proc.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()

                listener.close()


if __name__ == "__main__":
    unittest.main()
