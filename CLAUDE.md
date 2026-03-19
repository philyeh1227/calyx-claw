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

## Gemini CLI Proxy

`gemini_cli_proxy.py` is a local HTTP server that intercepts OpenClaw's Gemini REST API calls and routes them through the `/usr/bin/gemini` CLI tool. This exists because OpenClaw and the CLI share the same API key and quota pool — the proxy allows model selection per-request.

**Request flow**: OpenClaw → `http://127.0.0.1:8080` → proxy → `gemini -m <model> -p "<prompt>"` → SSE/JSON response back to OpenClaw

- Model name is extracted from the URL path (`/models/<model>:generateContent`)
- Both `generateContent` and `streamGenerateContent?alt=sse` endpoints are handled
- API key loaded from `~/.env` on the RPi5 at startup

## Config Files on RPi5

| File | Purpose |
|------|---------|
| `~/.openclaw/openclaw.json` | OpenClaw main config (model, proxy baseUrl, Discord channel) |
| `~/.gemini/settings.json` | gemini CLI default model (`selectedModel`) |
| `~/.env` | `GEMINI_API_KEY` for the proxy |
| `/etc/openclaw/openclaw.env` | OpenClaw systemd env vars |

**Editing openclaw.json** — use `python3 -c "import json; ..."` directly on the RPi5; `openclaw config set` has validation issues with nested provider keys.

## Current Model

`gemini-3.1-flash-lite` — set in both `openclaw.json` (`agents.defaults.model.primary`) and `~/.gemini/settings.json` (`selectedModel`). Both must be updated together when switching models.

## Discord Integration

- Bot name: `@clawclaw`
- Responds only when @mentioned in the configured channel
- Slash commands available to Discord user ID `876772650872090657`
- Channel allowlist: `876773293116497940/1483838752106479666`
