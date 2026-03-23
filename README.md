# RPi5 Claw — Install & Operations Log

Hardware: Raspberry Pi 5 8GB + Hailo-8L NPU
OS: Debian 13 (trixie) ARM64
Hostname: piclaw-2603001
Tailscale IP: 100.105.147.105
User: calyxclaw-one

---

## detect.py — YOLOv8 Hailo Object Detection + Camera Capture API

Entry point: `~/detect.py` on RPi5

Runs YOLOv8s on the Hailo-8L NPU with Picamera2 (1920x1080 main + 640x640 lores).
Keeps the latest full-resolution frame in a RAM buffer and exposes an HTTP capture API on port 8081.

```bash
# Start (background)
nohup python3 ~/detect.py > /tmp/detect.log 2>&1 &

# Check
pgrep -af detect.py
tail -f /tmp/detect.log

# Detection status (updated every 60s)
cat ~/hailo_yolov8/status.txt

# Stop
pkill -f detect.py

# Capture API health check
curl http://127.0.0.1:8081/health

# Trigger a photo capture
curl -s -X POST http://127.0.0.1:8081/capture | python3 -m json.tool
```

## capture_photo.py — Photo Capture Client + Discord Upload

Thin client for detect.py's capture API. Captures a photo from the RAM frame buffer and optionally uploads to Discord.

```bash
# Basic capture (returns JSON with file path)
python3 ~/capture_photo.py --json

# Capture and upload to Discord (auto-detects channel from /tmp/openclaw_channel_id)
python3 ~/capture_photo.py --discord --json

# Capture with custom message
python3 ~/capture_photo.py --discord --message "Hello!" --json

# Capture and upload to specific Discord channel
python3 ~/capture_photo.py --discord --channel 1485611366873174189 --json

# Capture and save a copy to a folder
python3 ~/capture_photo.py --copy-to ~/photos/ --json
```

Channel ID priority: `--channel` arg > `/tmp/openclaw_channel_id` file > `DISCORD_CHANNEL_ID` env var > hardcoded default.

### Discord photo via OpenClaw

In Discord, @mention the bot and say "拍照":

```
@clawclaw 拍照
```

The OpenClaw agent uses the `exec` tool to run `capture_photo.py`. The proxy automatically extracts the Discord channel ID from conversation metadata and writes it to `/tmp/openclaw_channel_id`, so photos are sent to the correct channel.

### Deploy

```bash
scp detect.py calyxclaw-one@100.105.147.105:~/detect.py
scp capture_photo.py calyxclaw-one@100.105.147.105:~/capture_photo.py
```

---

## OpenClaw Install Log — 2026-03-18

### 1. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
# 瀏覽器開啟授權連結後完成
tailscale status
```

### 2. 建立 OpenClaw 服務帳號與目錄

```bash
sudo useradd -r -m -d /var/lib/openclaw -s /usr/sbin/nologin openclaw
sudo mkdir -p /opt/openclaw /etc/openclaw /var/log/openclaw /srv/openclaw-work
sudo chown -R openclaw:openclaw /opt/openclaw /etc/openclaw /var/log/openclaw /srv/openclaw-work /var/lib/openclaw
```

### 3. 安裝 Node.js 22

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version   # v22.22.1
npm --version    # 10.9.4
```

### 4. 安裝 OpenClaw

```bash
sudo npm install -g openclaw@latest
openclaw --version   # OpenClaw 2026.3.13
```

### 5. 環境變數檔

```bash
sudo nano /etc/openclaw/openclaw.env
sudo chown openclaw:openclaw /etc/openclaw/openclaw.env
sudo chmod 600 /etc/openclaw/openclaw.env
```

內容：

```env
NODE_ENV=production
PORT=3000
OPENCLAW_DATA_DIR=/var/lib/openclaw
OPENCLAW_WORK_DIR=/srv/openclaw-work
OPENCLAW_LOG_DIR=/var/log/openclaw

GEMINI_API_KEY=<your_gemini_api_key>
GEMINI_MODEL=gemini-2.0-flash

OPENCLAW_MAX_CONCURRENCY=2
OPENCLAW_REQUEST_TIMEOUT_MS=90000
OPENCLAW_CONFIRM_DESTRUCTIVE=true
```

### 6. Gateway 初始化

```bash
# 安裝 gateway（生成 token、建立 user systemd service）
openclaw gateway install

# 設定模式為 local
openclaw config set gateway.mode local

# 設定綁定到 Tailscale 網卡
openclaw config set gateway.bind tailnet

# 啟動並設定開機自啟
systemctl --user enable --now openclaw-gateway
```

### 7. 驗證

```bash
openclaw gateway status
# Gateway: bind=tailnet (100.105.147.105), port=18789
# Listening: 100.105.147.105:18789
# RPC probe: ok
```

Dashboard（需透過 Tailscale）：`http://100.105.147.105:18789/`

---

## OpenClaw 使用

### 對話

```bash
# 終端機互動介面（推薦）
openclaw tui

# 單次傳訊息
openclaw agent --message "你好"
```

### 設定檔位置

| 檔案 | 用途 |
|------|------|
| `~/.openclaw/openclaw.json` | 主設定檔 |
| `~/.openclaw/workspace/SOUL.md` | Agent 人格設定 |
| `~/.openclaw/workspace/USER.md` | 使用者資訊 |
| `~/.openclaw/workspace/IDENTITY.md` | Agent 身份 |
| `~/.openclaw/workspace/BOOTSTRAP.md` | 啟動指令 |
| `~/.openclaw/memory/main.sqlite` | 記憶資料庫 |

### 常用 CLI 指令

```bash
openclaw tui                        # 終端機互動介面
openclaw agent --message "..."      # 單次對話
openclaw status                     # 顯示連線狀態
openclaw gateway status             # Gateway 詳細狀態
openclaw security audit             # 安全審計
openclaw models list                # 列出可用模型
openclaw sessions                   # 列出對話紀錄
openclaw memory search "..."        # 搜尋記憶
openclaw logs                       # 即時 log
openclaw doctor                     # 健康檢查與修復
openclaw config get <key>           # 讀取設定
openclaw config set <key> <value>   # 寫入設定
```

### 模型設定（目前）

目前使用 `gemini-3.1-flash-lite-preview`（REST API 名稱），設定分兩處，均為永久生效，開機不需重新設定：

**OpenClaw** (`~/.openclaw/openclaw.json`)：
```bash
python3 -c "
import json
path = '/home/calyxclaw-one/.openclaw/openclaw.json'
with open(path) as f:
    d = json.load(f)
d['agents']['defaults']['model']['primary'] = 'google/gemini-3.1-flash-lite-preview'
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
"
```

**gemini CLI** (`~/.gemini/settings.json`)：
```bash
python3 -c "
import json
path = '/home/calyxclaw-one/.gemini/settings.json'
with open(path) as f:
    d = json.load(f)
d['selectedModel'] = 'gemini-3.1-flash-lite-preview'
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
"
```

切換模型時兩處都要更新。REST API 模型名稱可能與 CLI 別名不同（如 `gemini-3.1-flash-lite` vs `gemini-3.1-flash-lite-preview`），用 ListModels API 確認。

### 工具權限設定

OpenClaw 預設停用 `exec`（shell 執行）工具。需在 `~/.openclaw/openclaw.json` 中明確啟用：

```bash
python3 -c "
import json
path = '/home/calyxclaw-one/.openclaw/openclaw.json'
with open(path) as f:
    d = json.load(f)
d['tools'] = {
    'profile': 'coding',
    'deny': [],
    'exec': {
        'host': 'gateway',
        'security': 'full',
        'ask': 'off'
    }
}
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
"
systemctl --user restart openclaw-gateway
```

| 欄位 | 說明 |
|------|------|
| `profile` | 工具集合：`coding` 包含 read/edit/write/exec 等 17 個工具 |
| `deny` | 拒絕清單，空陣列 = 不額外拒絕任何工具 |
| `exec.host` | 執行主機：`gateway` = 在 gateway 本機執行 |
| `exec.security` | 安全模式：`full` = 無沙箱限制，`sandbox` = 受限環境 |
| `exec.ask` | 執行前確認：`off` = 不詢問，`always` = 每次詢問 |

**注意**：`security: "full"` 允許 agent 執行任意 shell 指令，僅適用於受信任環境（如 Tailscale 私網內）。

### Skills

OpenClaw skills 放在 `~/.openclaw/skills/<name>/SKILL.md`，會自動注入 agent 的 system prompt。

```bash
# 查看已安裝的 skills
ls ~/.openclaw/skills/

# 目前已安裝
# capture_photo — 教 agent 使用 exec 工具執行 capture_photo.py 拍照
```

新增 skill：建立 `~/.openclaw/skills/<name>/SKILL.md`，重啟 gateway 生效。

---

## 常用維運指令

```bash
# OpenClaw gateway
systemctl --user status openclaw-gateway
systemctl --user restart openclaw-gateway
journalctl --user -u openclaw-gateway -n 50 --no-pager
openclaw gateway status

# 系統狀態
cat /sys/class/thermal/thermal_zone0/temp | awk '{printf "%.1f°C\n", $1/1000}'
free -h
df -h /
```

---

## Gemini REST Proxy

Reverse proxy that forwards complete Gemini REST API requests from OpenClaw to
`https://generativelanguage.googleapis.com`, preserving tools, function calling,
system instructions, and all other fields.

### Architecture

```
OpenClaw → http://127.0.0.1:8080 → gemini_cli_proxy.py → Gemini REST API → response back
```

### Files

| File | Location |
|------|----------|
| `gemini_cli_proxy.py` | `~/gemini_cli_proxy.py` on RPi5 |
| `gemini-cli-proxy.service` | `/etc/systemd/system/gemini-cli-proxy.service` |

### How it works

1. OpenClaw sends a POST to `/models/<model>:generateContent` or `:streamGenerateContent`
2. Proxy prepends `/v1beta` prefix if missing, appends `?key=<GEMINI_API_KEY>` to the URL
3. Complete request body is forwarded as-is (tools, function declarations, system instructions all preserved)
4. For `streamGenerateContent`: response chunks are streamed back via chunked transfer encoding
5. For `generateContent`: full JSON response is forwarded
6. Proxy extracts Discord channel ID from conversation metadata → writes to `/tmp/openclaw_channel_id`
7. Logs include tool names, functionCall presence, and response summaries

### Deploy & manage

```bash
# Deploy
scp gemini_cli_proxy.py calyxclaw-one@100.105.147.105:~/gemini_cli_proxy.py

# Restart systemd service
sudo systemctl restart gemini-cli-proxy

# Status & logs
sudo systemctl status gemini-cli-proxy
sudo journalctl -u gemini-cli-proxy -n 50 --no-pager
```

### Test

```bash
# Health check
curl http://127.0.0.1:8080/health

# Test with function calling
python3 -c "
import urllib.request, json
body = json.dumps({
    'contents': [{'role': 'user', 'parts': [{'text': 'Say hello'}]}],
    'tools': [{'functionDeclarations': [{'name': 'test', 'description': 'test', 'parameters': {'type': 'object', 'properties': {}}}]}]
}).encode()
req = urllib.request.Request('http://127.0.0.1:8080/v1beta/models/gemini-3.1-flash-lite-preview:generateContent', data=body, headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=30)
print(json.dumps(json.loads(resp.read()), indent=2))
"
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| Port 8080 already in use | `sudo systemctl restart gemini-cli-proxy` |
| `GEMINI_API_KEY not set` | Check `~/.env` contains `GEMINI_API_KEY=...` |
| `Unexpected end of JSON input` | Proxy ensures valid JSON on errors; check proxy logs |
| 404 on model name | REST API names differ from CLI — use ListModels API to verify |
| 429 quota exceeded | Free tier limit — wait for reset or switch to paid tier |
| Model ignores tools | Check proxy log for `tool names:` — verify tools are being forwarded |
| OpenClaw retrying with old error context | Delete stale session: `rm ~/.openclaw/agents/main/sessions/<id>.jsonl` |

---

## 鏡像到新機器

### Step 1 — 製作鏡像（在來源機或 PC 上執行）

```bash
# 確認來源磁碟位置（通常是 /dev/sda 或 /dev/mmcblk0）
lsblk

# 製作 image（來源機關機後，從另一台機器或用 SD card reader 操作）
sudo dd if=/dev/sdX of=~/rpi5-clone.img bs=4M status=progress conv=fsync

# 或壓縮版本（節省空間）
sudo dd if=/dev/sdX bs=4M status=progress | gzip > ~/rpi5-clone.img.gz
```

### Step 2 — 寫入新磁碟

```bash
# 寫入（替換 /dev/sdY 為目標磁碟）
sudo dd if=~/rpi5-clone.img of=/dev/sdY bs=4M status=progress conv=fsync

# 或從壓縮版本
gunzip -c ~/rpi5-clone.img.gz | sudo dd of=/dev/sdY bs=4M status=progress conv=fsync
```

### Step 3 — 新機器開機後必做（機器唯一設定）

```bash
# 1. 變更 hostname
sudo hostnamectl set-hostname <新機器名稱>
echo "127.0.1.1 <新機器名稱>" | sudo tee -a /etc/hosts

# 2. 重建 SSH host keys
sudo rm /etc/ssh/ssh_host_*
sudo dpkg-reconfigure openssh-server

# 3. 重新授權 Tailscale（取得新的 node IP）
sudo tailscale up --force-reauth
tailscale ip -4  # 確認新 IP

# 4. 重新初始化 OpenClaw gateway
openclaw gateway reset
openclaw gateway install
systemctl --user enable --now openclaw-gateway

# 5. 確認 proxy 正常（service 應該已隨鏡像帶過來）
sudo systemctl status gemini-cli-proxy
```

### Step 4 — 視需求更新 API key

```bash
# 若需要獨立 quota，替換 GEMINI_API_KEY
nano ~/.env
# 修改 GEMINI_API_KEY=<新的 key>

# 同步更新 OpenClaw env
sudo nano /etc/openclaw/openclaw.env
```

### 不需要變更的項目

- `gemini_cli_proxy.py` 和 systemd service — 直接沿用
- `~/.openclaw/openclaw.json` — model、proxy baseUrl 設定相同
- `~/.gemini/settings.json` — selectedModel 設定相同
- GEMINI_API_KEY — 可多台共用（但 quota 共享）

---

## 備註

- OpenClaw gateway 只綁定在 Tailscale IP，不對公網開放
- Gateway 密碼存放在 systemd service env（`OPENCLAW_GATEWAY_PASSWORD`），不在 config 檔
- AI 模型：Gemini 3.1 Flash Lite Preview（透過 gemini REST proxy 路由）
- Discord bot `@clawclaw` 已串接，@mention 觸發，slash commands 已對 user `876772650872090657` 開放
- Discord channels: `1483838752106479666`, `1485611366873174189`
- Hailo NPU 由 detect.py 獨立使用，與 OpenClaw 無資源衝突
- detect.py 同時提供物件偵測和 camera capture API（port 8081）
- Picamera2 RGB888 格式在記憶體中實際為 BGR，detect.py 在存 JPEG 前會做 `[:, :, ::-1]` 轉換
- OpenClaw 工具設定：`profile: "coding"`, `exec.security: "full"`, `exec.ask: "off"`
- OpenClaw skill `capture_photo` 教導 agent 使用 exec tool 執行 capture_photo.py
- Dashboard 需 HTTPS secure context，建議用 SSH tunnel 或 `openclaw tui`
- SD 卡鏡像已備份至外接 SSD（`/RPi5-Clone/rpi5-clone.img`，117GB，2026-03-19）
