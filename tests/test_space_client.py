from scripts.check_space_api import build_headers, parse_sse


def test_parse_sse_yields_events_in_order():
    lines = ["event: generating\n", 'data: ["a"]\n', "\n", "event: complete\r\n", 'data: [{"ok": true}]\r\n']
    assert list(parse_sse(lines)) == [("generating", '["a"]'), ("complete", '[{"ok": true}]')]


def test_parse_sse_handles_error_without_payload():
    assert list(parse_sse(["event: error\n", "data: null\n"])) == [("error", "null")]


def test_headers_include_token_only_when_given():
    assert "Authorization" not in build_headers(None)
    assert build_headers("hf_x")["Authorization"] == "Bearer hf_x"
