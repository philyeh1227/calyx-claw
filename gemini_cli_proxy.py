#!/usr/bin/env python3
"""
gemini_cli_proxy.py

Local reverse proxy: forwards complete Gemini REST API requests from
OpenClaw to https://generativelanguage.googleapis.com, preserving
tools, function calling, system instructions, and all other fields.

Deploy to RPi5:
    scp gemini_cli_proxy.py calyxclaw-one@100.105.147.105:~/gemini_cli_proxy.py

Run:
    python3 ~/gemini_cli_proxy.py

Then in OpenClaw config set baseUrl to http://127.0.0.1:8080
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
UPSTREAM = "https://generativelanguage.googleapis.com"
REQUEST_TIMEOUT = 120   # seconds
ENV_FILE = os.path.expanduser("~/.env")

API_KEY = ""  # loaded at startup


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


def extract_model(path: str) -> str:
    """Extract model name from path like /models/gemini-2.5-flash:generateContent"""
    m = re.search(r"/models/([^/:]+)", path)
    return m.group(1) if m else ""


def log_request_summary(body: dict, path: str):
    """Log a brief summary of the forwarded request."""
    model = extract_model(path)
    has_tools = "tools" in body or "tool_config" in body
    has_system = "systemInstruction" in body or "system_instruction" in body
    n_contents = len(body.get("contents", []))

    parts_summary = []
    if has_tools:
        tool_names = []
        for t in body.get("tools", []):
            decls = t.get("functionDeclarations", t.get("function_declarations", []))
            for d in decls:
                tool_names.append(d.get("name", "?"))
        parts_summary.append(f"tools={len(tool_names)}")
        print(f"[proxy] tool names: {', '.join(tool_names)}", file=sys.stderr, flush=True)
    if has_system:
        parts_summary.append("system=yes")
    parts_summary.append(f"turns={n_contents}")

    # extract last user text for preview
    last_text = ""
    for content in reversed(body.get("contents", [])):
        if content.get("role") == "user":
            for part in content.get("parts", []):
                if part.get("text"):
                    last_text = part["text"][:80].replace("\n", " ")
                    break
            if last_text:
                break

    # Dump first user content parts for debugging
    for content in body.get("contents", []):
        if content.get("role") == "user":
            all_text = ""
            for part in content.get("parts", []):
                all_text += part.get("text", "")
            if all_text:
                # Write to file to avoid journald truncation
                with open("/tmp/proxy_meta_dump.txt", "w") as df:
                    df.write(all_text)
                print(f"[proxy] USER_META dumped to /tmp/proxy_meta_dump.txt ({len(all_text)} chars)", file=sys.stderr, flush=True)
            break

    info = " ".join(parts_summary)
    print(f"[proxy] -> {model} [{info}] {last_text}...", file=sys.stderr, flush=True)

    # Log if capture_photo skill appears in system instruction
    sys_inst = body.get("systemInstruction", body.get("system_instruction", {}))
    sys_text = ""
    for p in sys_inst.get("parts", []):
        sys_text += p.get("text", "")
    if "capture_photo" in sys_text:
        print("[proxy] system prompt includes capture_photo skill", file=sys.stderr, flush=True)
    elif sys_text:
        print(f"[proxy] system prompt ({len(sys_text)} chars), NO capture_photo skill", file=sys.stderr, flush=True)


CHANNEL_ID_FILE = "/tmp/openclaw_channel_id"


def _extract_and_save_channel(body: dict):
    """Extract Discord channel ID from OpenClaw conversation metadata and save to file."""
    for content in body.get("contents", []):
        if content.get("role") != "user":
            continue
        for part in content.get("parts", []):
            text = part.get("text", "")
            # Look for "channel id:<digits>" in conversation_label
            m = re.search(r"channel id:(\d+)", text)
            if m:
                channel_id = m.group(1)
                try:
                    with open(CHANNEL_ID_FILE, "w") as f:
                        f.write(channel_id)
                except OSError:
                    pass
                return
        break


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[proxy] {fmt % args}", file=sys.stderr, flush=True)

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "backend": "gemini-rest-proxy"})
        else:
            self._send_json(404, {"error": {"message": "not found", "code": 404}})

    def _is_generate_endpoint(self) -> bool:
        return "generateContent" in self.path or "streamGenerateContent" in self.path

    def _is_streaming(self) -> bool:
        return "streamGenerateContent" in self.path

    def do_POST(self):
        if not self._is_generate_endpoint():
            self._send_json(404, {"error": {"message": "not found", "code": 404}})
            return

        # Read the complete request body
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b""

        # Log summary and extract channel ID
        try:
            body_dict = json.loads(raw_body) if raw_body else {}
            log_request_summary(body_dict, self.path)
            # Extract channel ID from conversation metadata and write to file
            # so capture_photo.py can read it
            _extract_and_save_channel(body_dict)
        except (json.JSONDecodeError, ValueError):
            pass

        # Build upstream URL: preserve the path, add API key
        # Ensure /v1beta prefix (OpenClaw may send /models/... without version)
        upstream_path = self.path
        if upstream_path.startswith("/models/"):
            upstream_path = "/v1beta" + upstream_path
        # Strip any existing key= param from the path (OpenClaw may send one)
        upstream_path = re.sub(r"[?&]key=[^&]*", "", upstream_path)
        separator = "&" if "?" in upstream_path else "?"
        upstream_url = f"{UPSTREAM}{upstream_path}{separator}key={API_KEY}"

        # Forward the request as-is
        req = urllib.request.Request(
            upstream_url,
            data=raw_body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"[proxy] <- upstream {e.code}: {error_body[:300]}", file=sys.stderr, flush=True)
            # Ensure we always return valid JSON to the client
            if not error_body.strip():
                error_body = json.dumps({"error": {"message": f"upstream returned {e.code}", "code": e.code}})
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body.encode())))
            self.end_headers()
            self.wfile.write(error_body.encode())
            return
        except urllib.error.URLError as e:
            msg = f"upstream connection error: {e.reason}"
            print(f"[proxy] <- {msg}", file=sys.stderr, flush=True)
            self._send_json(502, {"error": {"message": msg, "code": 502}})
            return
        except TimeoutError:
            print("[proxy] <- upstream timeout", file=sys.stderr, flush=True)
            self._send_json(504, {"error": {"message": "upstream timeout", "code": 504}})
            return

        # Forward upstream response back to client
        status = resp.status
        content_type = resp.headers.get("Content-Type", "application/json")

        if self._is_streaming():
            # Stream SSE chunks back to client
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            total = 0
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                # HTTP chunked encoding
                self.wfile.write(f"{len(chunk):x}\r\n".encode())
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                total += len(chunk)

            # Final chunk
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            print(f"[proxy] <- streamed {total} bytes", file=sys.stderr, flush=True)
        else:
            # Non-streaming: read full response and forward
            resp_body = resp.read()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

            # Log response summary
            try:
                resp_dict = json.loads(resp_body)
                candidates = resp_dict.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    has_fc = any("functionCall" in p or "function_call" in p for p in parts)
                    has_text = any("text" in p for p in parts)
                    finish = candidates[0].get("finishReason", "?")
                    print(f"[proxy] <- {status} finish={finish} text={has_text} functionCall={has_fc}",
                          file=sys.stderr, flush=True)
                else:
                    print(f"[proxy] <- {status} ({len(resp_body)} bytes)", file=sys.stderr, flush=True)
            except (json.JSONDecodeError, ValueError, KeyError):
                print(f"[proxy] <- {status} ({len(resp_body)} bytes)", file=sys.stderr, flush=True)


if __name__ == "__main__":
    load_env_file(ENV_FILE)
    API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not API_KEY:
        print("[proxy] FATAL: GEMINI_API_KEY not set", file=sys.stderr, flush=True)
        sys.exit(1)
    server = HTTPServer((PROXY_HOST, PROXY_PORT), Handler)
    print(f"[proxy] listening on {PROXY_HOST}:{PROXY_PORT}", file=sys.stderr, flush=True)
    print(f"[proxy] upstream: {UPSTREAM}", file=sys.stderr, flush=True)
    print(f"[proxy] API key: {API_KEY[:8]}...{API_KEY[-4:]}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] stopped.", file=sys.stderr)
