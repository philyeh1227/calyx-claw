# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Target System

All code in this repo is deployed and runs on a Raspberry Pi 5 (Debian 13 ARM64) reachable via Tailscale:

- **Host**: `calyxclaw-one@100.105.147.105`
- **SSH**: `ssh calyxclaw-one@100.105.147.105`
- **Deploy**: `scp <file> calyxclaw-one@100.105.147.105:~/`

## Key Services

| Service | Type | Control |
|---------|------|---------|
| `openclaw-gateway` | systemd user service | `systemctl --user [start\|stop\|restart\|status] openclaw-gateway` |
| `gemini-cli-proxy` | systemd system service | `sudo systemctl [start\|stop\|restart\|status] gemini-cli-proxy` |

## Gemini REST Proxy

`gemini_cli_proxy.py` is a local reverse proxy that forwards complete Gemini REST API requests from OpenClaw to `https://generativelanguage.googleapis.com`, preserving tools, function calling, system instructions, and all other fields.

**Request flow**: OpenClaw → `http://127.0.0.1:8080` → proxy → Gemini REST API → response back to OpenClaw

- Model name is extracted from the URL path (`/models/<model>:generateContent`)
- Automatically prepends `/v1beta` prefix when missing (OpenClaw sends `/models/...` without version)
- Both `generateContent` and `streamGenerateContent?alt=sse` endpoints are handled (streaming uses chunked transfer)
- API key loaded from `~/.env` on the RPi5 at startup, appended as `?key=` to upstream URL
- Extracts Discord channel ID from OpenClaw conversation metadata and writes to `/tmp/openclaw_channel_id` for use by `capture_photo.py`
- Logs tool names, request summaries, and response details (functionCall presence, finishReason) for debugging

## Config Files on RPi5

| File | Purpose |
|------|---------|
| `~/.openclaw/openclaw.json` | OpenClaw main config (model, proxy baseUrl, Discord channels, tools) |
| `~/.gemini/settings.json` | gemini CLI default model (`selectedModel`) |
| `~/.env` | `GEMINI_API_KEY` and `DISCORD_BOT_TOKEN` |
| `/etc/openclaw/openclaw.env` | OpenClaw systemd env vars |
| `~/.openclaw/skills/capture_photo/SKILL.md` | Skill teaching the agent to use the camera |

**Editing openclaw.json** — use `python3 -c "import json; ..."` directly on the RPi5; `openclaw config set` has validation issues with nested provider keys.

## Current Model

`gemini-3.1-flash-lite-preview` — set in both `openclaw.json` (`agents.defaults.model.primary` as `google/gemini-3.1-flash-lite-preview`) and `~/.gemini/settings.json` (`selectedModel`). Both must be updated together when switching models.

**Note**: REST API model names may differ from CLI aliases. Use `ListModels` API to verify. For example, `gemini-3.1-flash-lite` (CLI) → `gemini-3.1-flash-lite-preview` (REST API).

## Discord Integration

- Bot name: `@clawclaw`
- Responds only when @mentioned in configured channels
- Slash commands available to Discord user ID `876772650872090657`
- Channel allowlist: `876773293116497940/1483838752106479666`, `876773293116497940/1485611366873174189`

## Camera & Photo Capture

**Architecture**: `detect.py` runs YOLOv8 on Hailo-8L NPU and keeps the latest full-resolution frame (1920x1080 RGB888) in a RAM buffer. On-demand capture is handled via HTTP API on port 8081.

**Components**:
- `detect.py` — main detection loop + FrameBuffer + HTTP capture API (port 8081). Runs as a foreground process on RPi5 (`~/detect.py`)
- `capture_photo.py` — thin client that calls the capture API and optionally uploads to Discord via Bot REST API
- `~/.openclaw/skills/capture_photo/SKILL.md` — OpenClaw skill that teaches the agent to use `exec` tool to run `capture_photo.py`

**Photo capture flow**:
1. User says "拍照" in Discord → OpenClaw sends request to Gemini via proxy
2. Proxy extracts channel ID from conversation metadata → writes to `/tmp/openclaw_channel_id`
3. Model calls `exec` tool → runs `python3 ~/capture_photo.py --discord --json`
4. `capture_photo.py` calls `POST http://127.0.0.1:8081/capture` → `detect.py` saves buffered frame to JPEG
5. `capture_photo.py` reads channel ID from `/tmp/openclaw_channel_id` → uploads photo to that Discord channel via Bot REST API

**Color note**: Picamera2 `RGB888` outputs BGR in memory. `detect.py` converts `frame[:, :, ::-1]` before saving via PIL.

## OpenClaw Tools Configuration

```json
{
  "tools": {
    "profile": "coding",
    "deny": [],
    "exec": {
      "host": "gateway",
      "security": "full",
      "ask": "off"
    }
  }
}
```

OpenClaw exposes 17 tools to the model: `read, edit, write, exec, process, cron, sessions_list, sessions_history, sessions_send, sessions_yield, sessions_spawn, subagents, session_status, web_search, web_fetch, memory_search, memory_get`.
