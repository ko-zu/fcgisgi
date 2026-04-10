import unittest
from fcgisgi.asgi_adapter import ASGIAdapter

class TestASGIScope(unittest.TestCase):
    def setUp(self):
        self.adapter = ASGIAdapter(lambda s, r, send: None, lambda d: None, on_close=lambda: None)

    def test_path_unquoting(self):
        # Path with percent encoding
        params = [
            (b"REQUEST_METHOD", b"GET"),
            (b"PATH_INFO", b"/hello%20world%21"),
            (b"QUERY_STRING", b"a=b"),
        ]
        scope = self.adapter._build_scope(1, params)
        
        self.assertEqual(scope["path"], "/hello world!")
        self.assertEqual(scope["raw_path"], b"/hello%20world%21")
        self.assertEqual(scope["query_string"], b"a=b")

    def test_scheme_detection(self):
        # HTTP
        params_http = [(b"REQUEST_METHOD", b"GET"), (b"HTTPS", b"off")]
        scope_http = self.adapter._build_scope(1, params_http)
        self.assertEqual(scope_http["scheme"], "http")

        # HTTPS (on)
        params_https = [(b"REQUEST_METHOD", b"GET"), (b"HTTPS", b"on")]
        scope_https = self.adapter._build_scope(1, params_https)
        self.assertEqual(scope_https["scheme"], "https")

        # HTTPS (1)
        params_https_1 = [(b"REQUEST_METHOD", b"GET"), (b"HTTPS", b"1")]
        scope_https_1 = self.adapter._build_scope(1, params_https_1)
        self.assertEqual(scope_https_1["scheme"], "https")

    def test_multiple_headers(self):
        # Multiple headers with same name (e.g. Cookie)
        params = [
            (b"REQUEST_METHOD", b"GET"),
            (b"HTTP_COOKIE", b"session=123"),
            (b"HTTP_COOKIE", b"user=abc"),
            (b"HTTP_X_CUSTOM", b"val1"),
            (b"HTTP_X_CUSTOM", b"val2"),
        ]
        scope = self.adapter._build_scope(1, params)
        
        headers = scope["headers"]
        # Should contain two separate cookie entries and two custom entries
        self.assertIn((b"cookie", b"session=123"), headers)
        self.assertIn((b"cookie", b"user=abc"), headers)
        self.assertIn((b"x-custom", b"val1"), headers)
        self.assertIn((b"x-custom", b"val2"), headers)
        
        # Verify order/count
        self.assertEqual(len([h for h in headers if h[0] == b"cookie"]), 2)
        self.assertEqual(len([h for h in headers if h[0] == b"x-custom"]), 2)

    def test_metadata_prioritization(self):
        # First occurrence of REQUEST_METHOD should win
        params = [
            (b"REQUEST_METHOD", b"POST"),
            (b"REQUEST_METHOD", b"GET"), # Duplicate, should be ignored for method
        ]
        scope = self.adapter._build_scope(1, params)
        self.assertEqual(scope["method"], "POST")

    def test_http_version_normalization(self):
        # HTTP/1.1 -> 1.1
        scope = self.adapter._build_scope(1, [(b"SERVER_PROTOCOL", b"HTTP/1.1")])
        self.assertEqual(scope["http_version"], "1.1")

        # HTTP/2.0 -> 2
        scope = self.adapter._build_scope(1, [(b"SERVER_PROTOCOL", b"HTTP/2.0")])
        self.assertEqual(scope["http_version"], "2")

        # HTTP/3.0 -> 3
        scope = self.adapter._build_scope(1, [(b"SERVER_PROTOCOL", b"HTTP/3.0")])
        self.assertEqual(scope["http_version"], "3")

        # Unknown -> 1.1 default
        scope = self.adapter._build_scope(1, [(b"SERVER_PROTOCOL", b"FOO/BAR")])
        self.assertEqual(scope["http_version"], "1.1")

if __name__ == "__main__":
    unittest.main()
