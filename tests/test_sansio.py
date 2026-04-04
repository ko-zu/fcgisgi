import unittest
import struct
from fcgisgi.sansio import (
    FastCGIConnection, RequestStarted, ParamsReceived, StdinReceived,
    EndOfStdin, FCGI_BEGIN_REQUEST, FCGI_PARAMS, FCGI_STDIN, FCGI_VERSION_1,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)


class TestFastCGIConnection(unittest.TestCase):
    def test_request_started(self):
        conn = FastCGIConnection()

        # FCGI_BEGIN_REQUEST
        # role=1 (RESPONDER), flags=1 (KEEP_CONN)
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)

        events = conn.feed_data(header + content)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], RequestStarted)
        self.assertEqual(events[0].request_id, 1)
        self.assertEqual(events[0].role, 1)
        self.assertEqual(events[0].flags, 1)

    def test_params_received(self):
        conn = FastCGIConnection()

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        conn.feed_data(header + content)

        # Params: "NAME"="VALUE"
        params_content = b"\x04\x05NAMEVALUE"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_PARAMS, 1, len(params_content), 0)

        # Empty params record (EOF)
        header_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_PARAMS, 1, 0, 0)

        events = conn.feed_data(header + params_content + header_eof)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ParamsReceived)
        self.assertEqual(events[0].params, {b"NAME": b"VALUE"})

    def test_stdin_received(self):
        conn = FastCGIConnection()

        # Start request
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_BEGIN_REQUEST, 1, len(content), 0)
        conn.feed_data(header + content)

        # STDIN: "HELLO"
        stdin_content = b"HELLO"
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1,
                             FCGI_STDIN, 1, len(stdin_content), 0)

        events = conn.feed_data(header + stdin_content)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], StdinReceived)
        self.assertEqual(events[0].data, b"HELLO")

        # EOF STDIN
        header_eof = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_STDIN, 1, 0, 0)
        events = conn.feed_data(header_eof)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], EndOfStdin)


if __name__ == "__main__":
    unittest.main()
