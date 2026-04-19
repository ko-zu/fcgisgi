import unittest
from fcgisgi.asgi_adapter import ASGIAdapter


class TestASGIScope(unittest.TestCase):
    def setUp(self):
        self.adapter = ASGIAdapter(lambda s, r, send: None, lambda d: None, on_close=lambda: None)

    def test_path_decoding(self):
        # Path with percent encoded utf-8
        params = [
            (b"REQUEST_METHOD", b"GET"),
            (b"PATH_INFO", "/hello 世界!".encode("utf-8")),
            (b"REQUEST_URI", b"/hello%20%e4%b8%96%e7%95%8c%21?a=%e3%81%82"),
            (b"QUERY_STRING", b"a=%e3%81%82"),
        ]
        scope = self.adapter._build_scope(1, params)

        self.assertEqual(scope["root_path"], "")
        self.assertEqual(scope["path"], "/hello 世界!")
        self.assertEqual(scope["raw_path"], b"/hello%20%e4%b8%96%e7%95%8c%21")
        self.assertEqual(scope["query_string"], b"a=%e3%81%82")

    def test_path_decoding_with_scriptname(self):
        params = [
            (b"REQUEST_METHOD", b"GET"),
            (b"PATH_INFO", "/hello 世界!".encode("utf-8")),
            (
                b"REQUEST_URI",
                b"/%e3%81%82.py/hello%20%e4%b8%96%e7%95%8c%21?a=%e3%81%82",
            ),
            (b"QUERY_STRING", b"a=%e3%81%82"),
            (b"SCRIPT_NAME", "/あ.py".encode("utf-8")),
        ]
        scope = self.adapter._build_scope(1, params)

        self.assertEqual(scope["root_path"], "/あ.py")
        self.assertEqual(scope["path"], "/あ.py/hello 世界!")
        self.assertNotIn("raw_path", scope)
        self.assertEqual(scope["query_string"], b"a=%e3%81%82")

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
            (b"REQUEST_METHOD", b"GET"),  # Duplicate, should be ignored for method
        ]
        scope = self.adapter._build_scope(1, params)
        self.assertEqual(scope["method"], "POST")

    def test_fcgi_params_extension(self):
        params = [(b"VAR1", b"val1"), (b"VAR2", b"val2")]
        scope = self.adapter._build_scope(1, params)

        self.assertIn("extensions", scope)
        self.assertIn("fcgisgi", scope["extensions"])
        self.assertEqual(scope["extensions"]["fcgisgi"]["fcgi_params"], params)

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

    def test_client_server_info(self):
        params = [
            (b"REMOTE_ADDR", b"127.0.0.1"),
            (b"REMOTE_PORT", b"12345"),
            (b"SERVER_ADDR", b"192.168.1.1"),
            (b"SERVER_PORT", b"80"),
        ]
        scope = self.adapter._build_scope(1, params)

        self.assertEqual(scope["client"], ("127.0.0.1", 12345))
        self.assertEqual(scope["server"], ("192.168.1.1", 80))

        # Missing port case
        params_no_port = [
            (b"REMOTE_ADDR", b"127.0.0.1"),
            (b"SERVER_ADDR", b"192.168.1.1"),
        ]
        scope_no_port = self.adapter._build_scope(1, params_no_port)
        self.assertEqual(scope_no_port["client"], ("127.0.0.1", 0))
        self.assertEqual(scope_no_port["server"], ("192.168.1.1", None))

        # Missing both case
        scope_none = self.adapter._build_scope(1, [])
        self.assertNotIn("client", scope_none)
        self.assertNotIn("server", scope_none)


if __name__ == "__main__":
    unittest.main()
