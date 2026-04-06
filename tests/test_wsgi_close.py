import unittest
import struct
import time
import threading
from fcgisgi.sansio import (
    FCGI_VERSION_1, FCGI_BEGIN_REQUEST, FCGI_PARAMS,
    FCGI_HEADER_FORMAT, FCGI_BEGIN_REQUEST_BODY_FORMAT
)
from fcgisgi.wsgi_adapter import WSGIAdapter


class TestWSGIClose(unittest.TestCase):
    def test_wsgi_close_on_error(self):
        import logging
        logging.getLogger('fcgisgi.wsgi_adapter').setLevel(logging.CRITICAL)
        try:
            closed = False

            class Result:
                def __iter__(self):
                    yield b"part1"
                    # Connection loss will be simulated during this yield
                    yield b"part2"

                def close(self):
                    nonlocal closed
                    closed = True

            def app(environ, start_response):
                start_response('200 OK', [])
                return Result()

            def send_func(data):
                # Simulate connection loss by raising an error when trying to send data
                if b"part1" in data:
                    raise ConnectionError("Lost")

            adapter = WSGIAdapter(app, send_func, lambda f, args: threading.Thread(target=f, args=args).start(), lambda f, *args: f(*args))

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

            # Wait for thread
            time.sleep(0.2)

            self.assertTrue(closed, "Result.close() was not called")
        finally:
            logging.getLogger('fcgisgi.wsgi_adapter').setLevel(logging.NOTSET)

    def test_wsgi_close_on_abort(self):
        closed = False

        class Result:
            def __iter__(self):
                while True:
                    yield b"still running"
                    time.sleep(0.01)

            def close(self):
                nonlocal closed
                closed = True

        def app(environ, start_response):
            start_response('200 OK', [])
            return Result()

        adapter = WSGIAdapter(app, lambda d: None, lambda f, args: threading.Thread(target=f, args=args).start(), lambda f, *args: f(*args))

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

        # Now simulate abort from FastCGI
        from fcgisgi.sansio import FCGI_ABORT_REQUEST
        header_abort = struct.pack(
            FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_ABORT_REQUEST, 1, 0, 0)
        adapter.handle_data(header_abort)

        time.sleep(0.1)
        self.assertTrue(closed, "Result.close() was not called after Abort")


if __name__ == "__main__":
    unittest.main()
