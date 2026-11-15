import http.server
import threading
import urllib.error

import pytest

from scripts.check_space_api import build_headers, call_endpoint, parse_sse


def test_parse_sse_yields_events_in_order():
    lines = ["event: generating\n", 'data: ["a"]\n', "\n", "event: complete\r\n", 'data: [{"ok": true}]\r\n']
    assert list(parse_sse(lines)) == [("generating", '["a"]'), ("complete", '[{"ok": true}]')]


def test_parse_sse_handles_error_without_payload():
    assert list(parse_sse(["event: error\n", "data: null\n"])) == [("error", "null")]


def test_headers_include_token_only_when_given():
    assert "Authorization" not in build_headers(None)
    assert build_headers("hf_x")["Authorization"] == "Bearer hf_x"


def serve(handler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("mode", ["redirect on stream", "redirect on submit"])
def test_token_is_never_sent_to_a_redirect_target(mode):
    received = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'event: complete\ndata: [{"ok": true}]\n\n')

        do_POST = do_GET

        def log_message(self, *arguments):
            pass

    other = serve(Other)
    location = f"http://127.0.0.1:{other.server_port}/elsewhere"

    class Space(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if mode == "redirect on submit":
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"event_id": "e1"}')

        def do_GET(self):
            self.send_response(307)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *arguments):
            pass

    space = serve(Space)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            call_endpoint(f"http://127.0.0.1:{space.server_port}", "env_report", [], token="hf_test", timeout=10)
        assert "redirect refused" in str(caught.value)
        assert "hf_test" not in str(caught.value)
        assert received == []
    finally:
        space.shutdown()
        other.shutdown()
