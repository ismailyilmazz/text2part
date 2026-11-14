import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://ismailylmz283-text2part.hf.space"
ENDPOINTS = ("env_report", "gpu_probe")


def parse_sse(lines):
    event = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            yield event, line[len("data:") :].strip()


def build_headers(token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def call_endpoint(base_url: str, api_name: str, data: list, token: str | None = None, timeout: float = 600):
    url = f"{base_url.rstrip('/')}/gradio_api/call/{api_name}"
    headers = build_headers(token)
    body = json.dumps({"data": data}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        event_id = json.load(response)["event_id"]
    request = urllib.request.Request(f"{url}/{event_id}", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        lines = (raw.decode("utf-8") for raw in response)
        for event, payload in parse_sse(lines):
            if event == "complete":
                return json.loads(payload)
            if event == "error":
                raise RuntimeError(f"endpoint error: {payload}")
    raise RuntimeError("stream ended without a result")


def probe(base_url: str, api_name: str, token: str | None) -> dict:
    started = time.perf_counter()
    entry = {"endpoint": api_name, "authenticated": bool(token)}
    try:
        outputs = call_endpoint(base_url, api_name, [], token)
        entry["result"] = outputs[0] if outputs else None
        entry["ok"] = isinstance(entry["result"], dict) and bool(entry["result"].get("ok"))
        if not entry["ok"] and isinstance(entry["result"], dict) and entry["result"].get("error"):
            entry["error"] = entry["result"]["error"]
    except (urllib.error.URLError, RuntimeError, KeyError, ValueError) as error:
        entry["ok"] = False
        entry["error"] = f"{type(error).__name__}: {error}"
    entry["seconds"] = round(time.perf_counter() - started, 2)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.check_space_api")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default=os.getenv("HF_TOKEN"))
    args = parser.parse_args(argv)
    modes = [args.token, None] if args.token else [None]
    results = [probe(args.url, api_name, token) for token in modes for api_name in ENDPOINTS]
    summary = [
        {key: entry.get(key) for key in ("endpoint", "authenticated", "ok", "seconds", "error")} for entry in results
    ]
    print(json.dumps({"url": args.url, "summary": summary, "details": results}, indent=2))
    return 0 if all(entry["ok"] for entry in results if entry["authenticated"] or not args.token) else 1


if __name__ == "__main__":
    sys.exit(main())
