import contextlib
import http.server
import io
import json
import threading
import urllib.error
import urllib.parse

import pytest

from scripts import check_space_api
from scripts.check_space_api import APPROVED_URL, build_headers, call_endpoint, main, parse_sse, token_allowed

TOKEN = "hf_test_value"


def test_parse_sse_yields_events_in_order():
    lines = ["event: generating\n", 'data: ["a"]\n', "\n", "event: complete\r\n", 'data: [{"ok": true}]\r\n']
    assert list(parse_sse(lines)) == [("generating", '["a"]'), ("complete", '[{"ok": true}]')]


def test_parse_sse_handles_error_without_payload():
    assert list(parse_sse(["event: error\n", "data: null\n"])) == [("error", "null")]


def test_headers_include_token_only_when_given():
    assert "Authorization" not in build_headers(None)
    assert build_headers("hf_x")["Authorization"] == "Bearer hf_x"


def test_approved_address_is_https_with_no_extras():
    parts = urllib.parse.urlsplit(APPROVED_URL)
    assert parts.scheme == "https"
    assert parts.hostname == "ismailylmz283-text2part.hf.space"
    assert (parts.port, parts.username, parts.password, parts.path, parts.query, parts.fragment) == (
        None,
        None,
        None,
        "",
        "",
        "",
    )
    assert check_space_api.DEFAULT_URL == APPROVED_URL


@pytest.mark.parametrize("url", [APPROVED_URL, APPROVED_URL + "/"])
def test_token_is_allowed_for_the_approved_address(url):
    assert token_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://ismailylmz283-text2part.hf.space",
        "https://localhost",
        "http://localhost:7860",
        "http://127.0.0.1:7860",
        "https://127.0.0.1",
        "https://[::1]",
        "https://huggingface.co/spaces/ismailylmz283/text2part",
        "https://ismailylmz283-arxiv-rag.hf.space",
        "https://other-user-text2part.hf.space",
        "https://hf.space",
        "https://ismailylmz283-text2part.hf.space.example.com",
        "https://ismailylmz283-text2part.hf.spaces",
        "https://ismailylmz283-text2part-hf.space",
        "https://ismailylmz283-text2part.hf.space@example.com",
        "https://user@ismailylmz283-text2part.hf.space",
        "https://ismailylmz283-text2part.hf.space:8443",
        "https://ismailylmz283-text2part.hf.space/other",
        "https://ismailylmz283-text2part.hf.space?next=https://example.com",
        "https://ismailylmz283-text2part.hf.space#fragment",
        "https://ismailylmz283-text2рart.hf.space",
        "https://ismailylmz283-text2part.hf.ѕpace",
        "https://xn--ismailylmz283-text2art-obs.hf.space",
        "https://ISMAILYLMZ283-TEXT2PART.HF.SPACE",
        "https://ismailylmz283-text2part.hf.space.",
        " https://ismailylmz283-text2part.hf.space",
        "https://ismailylmz283-text2part.hf.space ",
        "ismailylmz283-text2part.hf.space",
        "//ismailylmz283-text2part.hf.space",
        "file:///etc/passwd",
        "",
    ],
)
def test_token_is_refused_for_any_other_address(url):
    assert not token_allowed(url)
    with pytest.raises(ValueError, match="approved Space address"):
        call_endpoint(url, "env_report", [], token=TOKEN, timeout=1)


def test_lookalike_example_is_the_real_punycode_of_the_cyrillic_name():
    assert "ismailylmz283-text2\u0440art".encode("idna") == b"xn--ismailylmz283-text2art-obs"


def test_non_text_addresses_are_refused():
    for value in (None, b"https://ismailylmz283-text2part.hf.space", 42):
        assert not token_allowed(value)


class Recorder:
    def __init__(self):
        self.requests = []


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def respond(self, status, body=b"", headers=()):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


@contextlib.contextmanager
def running(handler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def gradio_handler(recorder):
    class Handler(QuietHandler):
        def do_POST(self):
            recorder.requests.append((self.command, self.path, self.headers.get("Authorization")))
            self.respond(200, b'{"event_id": "e1"}')

        def do_GET(self):
            recorder.requests.append((self.command, self.path, self.headers.get("Authorization")))
            self.respond(200, b'event: complete\ndata: [{"ok": true}]\n\n')

    return Handler


class FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if request.get_method() == "POST":
            return io.BytesIO(b'{"event_id": "e1"}')
        return io.BytesIO(b'event: complete\ndata: [{"ok": true}]\n\n')


def authorization_headers(requests):
    return [value for request in requests for name, value in request.header_items() if name.lower() == "authorization"]


def test_custom_address_works_without_a_token():
    recorder = Recorder()
    with running(gradio_handler(recorder)) as address:
        result = call_endpoint(address, "env_report", [], timeout=10)
    assert result == [{"ok": True}]
    assert [item[0] for item in recorder.requests] == ["POST", "GET"]
    assert all(item[2] is None for item in recorder.requests)


def test_token_is_refused_before_any_request_is_made(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr(check_space_api, "OPENER", opener)
    with pytest.raises(ValueError):
        call_endpoint("http://127.0.0.1:7860", "env_report", [], token=TOKEN, timeout=10)
    assert opener.requests == []


def test_main_withholds_the_token_for_a_custom_address(capsys, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    opener = FakeOpener()
    monkeypatch.setattr(check_space_api, "OPENER", opener)
    address = "http://127.0.0.1:7860"
    assert main(["--url", address]) == 0
    assert main(["--url", address, "--token", TOKEN]) == 0
    output = capsys.readouterr().out
    assert TOKEN not in output
    reports = [json.loads(part) for part in output.replace("}\n{", "}\n\x00{").split("\x00")]
    assert [report["token_withheld"] for report in reports] == [True, True]
    assert all(entry["authenticated"] is False for report in reports for entry in report["summary"])
    assert len(opener.requests) == 8
    assert all(request.full_url.startswith(address + "/gradio_api/call/") for request in opener.requests)
    assert authorization_headers(opener.requests) == []


def test_main_sends_the_token_only_to_the_approved_address(capsys, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    opener = FakeOpener()
    monkeypatch.setattr(check_space_api, "OPENER", opener)
    assert main(["--url", APPROVED_URL, "--token", TOKEN]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["token_withheld"] is False
    assert [entry["authenticated"] for entry in report["summary"]] == [True, True, False, False]
    assert all(request.full_url.startswith(APPROVED_URL + "/gradio_api/call/") for request in opener.requests)
    assert authorization_headers(opener.requests) == [f"Bearer {TOKEN}"] * 4


def test_main_without_any_token_reports_nothing_withheld(capsys, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    opener = FakeOpener()
    monkeypatch.setattr(check_space_api, "OPENER", opener)
    assert main(["--url", "http://127.0.0.1:7860"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["token_withheld"] is False and "note" not in report
    assert len(opener.requests) == 4
    assert authorization_headers(opener.requests) == []


@pytest.mark.parametrize("mode", ["redirect on stream", "redirect on submit"])
def test_token_is_never_sent_to_a_redirect_target(mode, monkeypatch):
    received = Recorder()
    with running(gradio_handler(received)) as other:
        location = other + "/elsewhere"

        class Space(QuietHandler):
            def do_POST(self):
                if mode == "redirect on submit":
                    self.respond(302, headers=[("Location", location)])
                else:
                    self.respond(200, b'{"event_id": "e1"}')

            def do_GET(self):
                self.respond(307, headers=[("Location", location)])

        with running(Space) as approved:
            monkeypatch.setattr(check_space_api, "APPROVED_URL", approved)
            assert token_allowed(approved)
            with pytest.raises(urllib.error.HTTPError) as caught:
                call_endpoint(approved, "env_report", [], token=TOKEN, timeout=10)
    assert "redirect refused" in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert received.requests == []
