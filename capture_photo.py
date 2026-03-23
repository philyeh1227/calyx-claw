#!/usr/bin/env python3
"""
capture_photo.py

Client for detect.py's capture API.
Triggers a photo capture from the RAM frame buffer and optionally
uploads it to Discord or copies it to another location.

Usage:
    python3 ~/capture_photo.py                                # capture, print path
    python3 ~/capture_photo.py --discord                      # capture + upload to Discord
    python3 ~/capture_photo.py --discord --message "Hello!"   # capture + upload with caption
    python3 ~/capture_photo.py --path /home/user/photo.jpg    # save to specific path
    python3 ~/capture_photo.py --copy-to /home/user/photos/   # capture + copy to folder

Deploy:
    scp capture_photo.py calyxclaw-one@100.105.147.105:~/capture_photo.py

Requires:
    - detect.py running (provides the capture API on port 8081)
    - requests: pip install requests --break-system-packages
    - DISCORD_BOT_TOKEN in ~/.env (only for --discord)
"""

import argparse
import json
import os
import shutil
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CAPTURE_API = "http://127.0.0.1:8081"
DISCORD_API = "https://discord.com/api/v10"
DISCORD_CHANNEL_ID_DEFAULT = "1483838752106479666"
CHANNEL_ID_FILE = "/tmp/openclaw_channel_id"
ENV_FILE = os.path.expanduser("~/.env")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_env(path: str):
    """Load key=value pairs from an env file into os.environ."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def capture(path: str = None, quality: int = 85) -> dict:
    """Request a photo capture from detect.py's API.

    Returns dict with keys: path, timestamp, size_bytes.
    """
    body = {}
    if path:
        body["path"] = path
    if quality != 85:
        body["quality"] = quality

    resp = requests.post(
        "{}/capture".format(CAPTURE_API), json=body, timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            "capture API error {}: {}".format(resp.status_code, resp.text[:300])
        )
    return resp.json()


def upload_to_discord(photo_path: str, message: str = "", channel_id: str = "") -> dict:
    """Upload a photo to a Discord channel via bot REST API."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in ~/.env")

    # Priority: explicit arg > file from proxy > env var > hardcoded default
    target_channel = channel_id
    if not target_channel:
        try:
            with open(CHANNEL_ID_FILE) as f:
                target_channel = f.read().strip()
        except (OSError, FileNotFoundError):
            pass
    if not target_channel:
        target_channel = os.environ.get("DISCORD_CHANNEL_ID", DISCORD_CHANNEL_ID_DEFAULT)
    url = "{}/channels/{}/messages".format(DISCORD_API, target_channel)
    headers = {"Authorization": "Bot {}".format(token)}

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    content = message if message else "Photo captured at {}".format(timestamp)
    filename = "claw_{}.jpg".format(time.strftime("%Y%m%d_%H%M%S"))

    with open(photo_path, "rb") as f:
        files = {"files[0]": (filename, f, "image/jpeg")}
        payload = {"content": content}
        resp = requests.post(
            url, headers=headers, data=payload, files=files, timeout=30,
        )

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            "Discord API {}: {}".format(resp.status_code, resp.text[:300])
        )
    return resp.json()


def copy_photo(src: str, dest_dir: str) -> str:
    """Copy a captured photo to a destination directory."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))
    shutil.copy2(src, dest)
    return dest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Capture a photo from detect.py and optionally share it.",
    )
    parser.add_argument(
        "--discord", action="store_true",
        help="Upload the photo to Discord after capture",
    )
    parser.add_argument(
        "--message", "-m", default="",
        help="Caption for the Discord upload",
    )
    parser.add_argument(
        "--path", "-p", default=None,
        help="Save photo to this specific file path",
    )
    parser.add_argument(
        "--copy-to", default=None,
        help="Copy captured photo to this directory",
    )
    parser.add_argument(
        "--channel", default="",
        help="Discord channel ID to upload to (default: built-in channel)",
    )
    parser.add_argument(
        "--quality", "-q", type=int, default=85,
        help="JPEG quality 1-100 (default: 85)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON (for programmatic use)",
    )
    args = parser.parse_args()

    load_env(ENV_FILE)

    output = {}

    try:
        # 1. Capture
        result = capture(path=args.path, quality=args.quality)
        photo_path = result["path"]
        output["capture"] = result

        if not args.json:
            print("[capture] {} ({} KB)".format(
                photo_path, result["size_bytes"] // 1024,
            ), flush=True)

        # 2. Copy (optional)
        if args.copy_to:
            copied = copy_photo(photo_path, args.copy_to)
            output["copied_to"] = copied
            if not args.json:
                print("[copy] -> {}".format(copied), flush=True)

        # 3. Discord upload (optional)
        if args.discord:
            dc_result = upload_to_discord(photo_path, args.message, args.channel)
            output["discord"] = {
                "message_id": dc_result.get("id"),
                "channel_id": dc_result.get("channel_id"),
            }
            if not args.json:
                print("[discord] Posted! Message ID: {}".format(
                    dc_result.get("id"),
                ), flush=True)

        # JSON output for programmatic use (e.g. OpenClaw tool)
        if args.json:
            print(json.dumps(output))

    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print("[ERROR] {}".format(e), file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
