#!/usr/bin/env python3
"""
gemini_cli_proxy.py

Local HTTP proxy: accepts Gemini REST API requests from OpenClaw,
routes them through the `gemini` CLI tool, returns responses in
Gemini API format.

Deploy to RPi5:
    scp gemini_cli_proxy.py calyxclaw-one@100.105.147.105:~/gemini_cli_proxy.py

Run:
    python3 ~/gemini_cli_proxy.py

Then in OpenClaw env (/etc/openclaw/openclaw.env) add:
    GEMINI_BASE_URL=http://127.0.0.1:8080
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
GEMINI_CLI = "/usr/bin/gemini"
CLI_TIMEOUT = 120       # seconds
ENV_FILE = os.path.expanduser("~/.env")


def load_env_file(path: str):
    """Load key=value pairs from an env file into os.environ."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
        print(f"[proxy] loaded env from {path}", file=sys.stderr, flush=True)
    except FileNotFoundError:
        print(f"[proxy] env file not found: {path}", file=sys.stderr, flush=True)
    except PermissionError:
        print(f"[proxy] no permission to read: {path}", file=sys.stderr, flush=True)


def extract_prompt(body: dict) -> str:
    """Pull all text parts from a generateContent request body."""
    parts = []
    for content in body.get("contents", []):
        role = content.get("role", "user")
        for part in content.get("parts", []):
            text = part.get("text", "")
            if text:
                if role == "user":
                    parts.append(text)
                else:
                    # include model turns as context prefix
                    parts.append(f"[assistant]: {text}")
    return "\n".join(parts)


def gemini_response(text: str) -> dict:
    """Wrap plain text into Gemini generateContent response format."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                    "role": "model",
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 0,
            "candidatesTokenCount": 0,
            "totalTokenCount": 0,
        },
    }


def extract_model(path: str) -> str:
    """Extract model name from path like /models/gemini-2.5-flash:generateContent"""
    import re as _re
    m = _re.search(r"/models/([^/:]+)", path)
    return m.group(1) if m else ""


def call_gemini_cli(prompt: str, model: str = "") -> str:
    import re
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    cmd = [GEMINI_CLI]
    if model:
        cmd += ["-m", model]
    cmd += ["-p", prompt]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
        env=env,
    )
    # strip ANSI escape codes
    stdout = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", result.stdout).strip()
    stderr = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", result.stderr).strip()
    print(f"[proxy] CLI exit={result.returncode} stdout={repr(stdout[:200])} stderr={repr(stderr[:200])}", file=sys.stderr, flush=True)
    if result.returncode != 0:
        raise RuntimeError(stderr or f"exit code {result.returncode}")
    return stdout


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[proxy] {fmt % args}", file=sys.stderr, flush=True)

    def send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "backend": "gemini-cli"})
        else:
            self.send_json(404, {"error": {"message": "not found", "code": 404}})

    def _is_generate_endpoint(self) -> bool:
        return "generateContent" in self.path or "streamGenerateContent" in self.path

    def _is_streaming(self) -> bool:
        return "streamGenerateContent" in self.path

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def _run_and_reply(self, prompt: str):
        preview = prompt[:80].replace("\n", " ")
        model = extract_model(self.path)
        print(f"[proxy] model={model!r} prompt ({len(prompt)} chars): {preview}...", file=sys.stderr, flush=True)
        try:
            text = call_gemini_cli(prompt, model)
            print(f"[proxy] response ({len(text)} chars)", file=sys.stderr, flush=True)
        except subprocess.TimeoutExpired:
            self.send_json(504, {"error": {"message": "gemini CLI timed out", "code": 504}})
            return
        except RuntimeError as e:
            self.send_json(500, {"error": {"message": str(e), "code": 500}})
            return

        if self._is_streaming():
            # Gemini SSE format: content chunk + finish chunk
            content_chunk = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": text}], "role": "model"},
                        "index": 0,
                    }
                ],
                "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
            }
            finish_chunk = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": ""}], "role": "model"},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ],
                "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
            }
            sse = (
                f"data: {json.dumps(content_chunk)}\r\n\r\n"
                f"data: {json.dumps(finish_chunk)}\r\n\r\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(sse)))
            self.end_headers()
            self.wfile.write(sse)
        else:
            self.send_json(200, gemini_response(text))

    def do_POST(self):
        if not self._is_generate_endpoint():
            self.send_json(404, {"error": {"message": "not found", "code": 404}})
            return

        try:
            body = self._read_body()
        except Exception as e:
            self.send_json(400, {"error": {"message": f"bad request: {e}", "code": 400}})
            return

        prompt = extract_prompt(body)
        if not prompt:
            self.send_json(400, {"error": {"message": "no prompt text", "code": 400}})
            return

        self._run_and_reply(prompt)


if __name__ == "__main__":
    load_env_file(ENV_FILE)
    if not os.environ.get("GEMINI_API_KEY"):
        print("[proxy] WARNING: GEMINI_API_KEY not set", file=sys.stderr, flush=True)
    server = HTTPServer((PROXY_HOST, PROXY_PORT), Handler)
    print(f"[proxy] listening on {PROXY_HOST}:{PROXY_PORT}", file=sys.stderr, flush=True)
    print(f"[proxy] using CLI: {GEMINI_CLI}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] stopped.", file=sys.stderr)
