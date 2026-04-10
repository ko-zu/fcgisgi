import unittest
import threading
from fcgisgi.wsgi_adapter import WSGIAdapter, WSGIRequest, WSGIInput
from fcgisgi.sansio import ParamsReceived, RequestStarted, FCGI_RESPONDER


class TestWSGIEnviron(unittest.TestCase):
    def setUp(self):
        # Basic mocks for WSGIAdapter requirements
        def dummy_send(d): pass
        def dummy_spawn(f, args): pass
        def dummy_call_soon(f, *args): f(*args)
        def dummy_on_close(): pass

        self.adapter = WSGIAdapter(
            application=lambda e, s: [],
            send_func=dummy_send,
            spawn_func=dummy_spawn,
            call_soon_func=dummy_call_soon,
            on_close=dummy_on_close
        )

    def test_metadata_prioritization(self):
        # First occurrence of REQUEST_METHOD should win
        self.adapter._requests[1] = WSGIRequest(id=1, stdin=WSGIInput())
        params_event = ParamsReceived(1, [
            (b"REQUEST_METHOD", b"POST"),
            (b"REQUEST_METHOD", b"GET"),
        ])
        self.adapter.handle_event(params_event)

        environ = self.adapter._requests[1].params
        self.assertEqual(environ["REQUEST_METHOD"], "POST")

    def test_header_merging_discard(self):
        # Duplicates of certain headers should be discarded (first one wins)
        self.adapter._requests[1] = WSGIRequest(id=1, stdin=WSGIInput())
        params_event = ParamsReceived(1, [
            (b"CONTENT_TYPE", b"application/json"),
            (b"CONTENT_TYPE", b"text/plain"),
            (b"HTTP_USER_AGENT", b"Mozilla/5.0"),
            (b"HTTP_USER_AGENT", b"Curl/7.0"),
        ])
        self.adapter.handle_event(params_event)

        environ = self.adapter._requests[1].params
        self.assertEqual(environ["CONTENT_TYPE"], "application/json")
        self.assertEqual(environ["HTTP_USER_AGENT"], "Mozilla/5.0")

    def test_header_merging_cookie(self):
        # Cookie headers should be joined with "; "
        self.adapter._requests[1] = WSGIRequest(id=1, stdin=WSGIInput())
        params_event = ParamsReceived(1, [
            (b"HTTP_COOKIE", b"a=1"),
            (b"HTTP_COOKIE", b"b=2"),
        ])
        self.adapter.handle_event(params_event)

        environ = self.adapter._requests[1].params
        self.assertEqual(environ["HTTP_COOKIE"], "a=1; b=2")

    def test_header_merging_comma(self):
        # Other headers should be joined with ", "
        self.adapter._requests[1] = WSGIRequest(id=1, stdin=WSGIInput())
        params_event = ParamsReceived(1, [
            (b"HTTP_X_FORWARDED_FOR", b"1.2.3.4"),
            (b"HTTP_X_FORWARDED_FOR", b"5.6.7.8"),
        ])
        self.adapter.handle_event(params_event)

        environ = self.adapter._requests[1].params
        self.assertEqual(environ["HTTP_X_FORWARDED_FOR"], "1.2.3.4, 5.6.7.8")

    def test_path_encoding(self):
        # PATH_INFO should be decoded correctly (latin-1 for WSGI as per spec)
        self.adapter._requests[1] = WSGIRequest(id=1, stdin=WSGIInput())
        utf8_path = "/テスト".encode("utf-8")
        quoted_path = b"/%E3%83%86%E3%82%B9%E3%83%88"
        params_event = ParamsReceived(1, [
            (b"REQUEST_METHOD", b"GET"),
            (b"PATH_INFO", utf8_path),
            (b"REQUEST_URI", quoted_path)
        ])
        self.adapter.handle_event(params_event)

        environ = self.adapter._requests[1].params
        # WSGI spec (PEP 3333) says it should be latin-1 decoded bytes
        self.assertEqual(environ["PATH_INFO"], utf8_path.decode('latin-1'))
        self.assertEqual(environ["REQUEST_URI"], quoted_path.decode('latin-1'))

    def test_wsgi_flags(self):
        # Test if wsgi.multiprocess is True
        req = WSGIRequest(id=1, stdin=WSGIInput(), params={
                          "REQUEST_METHOD": "GET"})
        self.adapter._requests[1] = req

        # We need to run _run_app partially or just check where flags are set.
        # Since _run_app is what sets the flags in environ, let's test it there.
        # But _run_app runs the app, so we'll provide a mock app.
        recorded_environ = {}

        def mock_app(environ, start_response):
            nonlocal recorded_environ
            recorded_environ = environ
            start_response('200 OK', [])
            return []

        self.adapter.application = mock_app
        self.adapter._run_app(req)

        self.assertTrue(recorded_environ.get("wsgi.multiprocess"))
        self.assertTrue(recorded_environ.get("wsgi.multithread"))


if __name__ == "__main__":
    unittest.main()
