# RPi5 Claw — Install & Operations Log

Hardware: Raspberry Pi 5 8GB + Hailo-8L NPU
OS: Debian 13 (trixie) ARM64
Hostname: piclaw-2603001
Tailscale IP: 100.105.147.105
User: calyxclaw-one

---

## detect.py — YOLOv8 Hailo Object Detection

Entry point: `/home/calyxclaw-one/hailo_yolov8/detect.py`

```bash
# 背景啟動
nohup python3 /home/calyxclaw-one/hailo_yolov8/detect.py > /tmp/detect_console.log 2>&1 &

# 確認運行
pgrep -a -f detect.py

# 查看 console log
tail -f /tmp/detect_console.log

# 查看偵測結果（每 60 秒更新）
cat /home/calyxclaw-one/hailo_yolov8/status.txt

# 停止
pkill -f detect.py
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

目前使用 `gemini-3.1-flash-lite`，設定分兩處，均為永久生效，開機不需重新設定：

**OpenClaw** (`~/.openclaw/openclaw.json`)：
```bash
python3 -c "
import json
path = '/home/calyxclaw-one/.openclaw/openclaw.json'
with open(path) as f:
    d = json.load(f)
d['agents']['defaults']['model']['primary'] = 'google/gemini-3.1-flash-lite'
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
d['selectedModel'] = 'gemini-3.1-flash-lite'
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
"
```

切換模型時兩處都要更新。

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

## Gemini CLI Proxy

Routes OpenClaw's Gemini REST API calls through the local `gemini` CLI tool instead
of hitting the Gemini REST API directly. Useful when the REST API key has quota issues
but the CLI (authenticated separately) works fine.

### Architecture

```
OpenClaw → http://127.0.0.1:8080 → gemini_cli_proxy.py → /usr/bin/gemini -m <model> -p "<prompt>"
```

### Files

| File | Location |
|------|----------|
| `gemini_cli_proxy.py` | `~/gemini_cli_proxy.py` on RPi5 |
| `gemini-cli-proxy.service` | `/etc/systemd/system/gemini-cli-proxy.service` (optional) |

### How it works

1. OpenClaw sends a POST to `/models/<model>:generateContent` or `/models/<model>:streamGenerateContent`
2. The proxy extracts the model name from the URL path and all `text` parts from the request body
3. It calls `gemini -m <model> -p "<prompt>"` as a subprocess with `NO_COLOR=1 TERM=dumb`
4. ANSI escape codes are stripped from the CLI output
5. For `streamGenerateContent`: response is wrapped in SSE format (two chunks: content + `finishReason: STOP`)
6. For `generateContent`: response is wrapped in standard Gemini JSON format

### OpenClaw config (`~/.openclaw/openclaw.json`)

```json
{
  "models": {
    "providers": {
      "google": {
        "baseUrl": "http://127.0.0.1:8080",
        "models": []
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "google/gemini-3.1-flash-lite"
      }
    }
  }
}
```

Edit with:
```bash
python3 -c "
import json
path = '/home/calyxclaw-one/.openclaw/openclaw.json'
with open(path) as f:
    d = json.load(f)
d['models']['providers']['google']['baseUrl'] = 'http://127.0.0.1:8080'
d['models']['providers']['google']['models'] = []
d['agents']['defaults']['model']['primary'] = 'google/gemini-3.1-flash-lite'
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
"
```

### API key setup

The proxy reads `~/.env` at startup to load `GEMINI_API_KEY`:

```bash
# Copy key from OpenClaw env
grep GEMINI_API_KEY /etc/openclaw/openclaw.env >> ~/.env
```

### Deploy & run

```bash
# Deploy
scp gemini_cli_proxy.py calyxclaw-one@100.105.147.105:~/gemini_cli_proxy.py

# Start (manual, background)
nohup python3 ~/gemini_cli_proxy.py > /tmp/proxy.log 2>&1 & disown

# Check
pgrep -a python3 | grep proxy
tail -f /tmp/proxy.log

# Stop
pkill -f gemini_cli_proxy.py
```

### Install as systemd service (persistent across reboots)

```bash
scp gemini-cli-proxy.service calyxclaw-one@100.105.147.105:~/
ssh calyxclaw-one@100.105.147.105 "sudo mv ~/gemini-cli-proxy.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now gemini-cli-proxy"

# Status
sudo systemctl status gemini-cli-proxy
journalctl -u gemini-cli-proxy -n 50 --no-pager
```

### Test

```bash
# Health check
curl http://127.0.0.1:8080/health

# Streaming (what OpenClaw uses)
curl -s -X POST 'http://127.0.0.1:8080/models/gemini-3.1-flash-lite:streamGenerateContent?alt=sse' \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"role":"user","parts":[{"text":"say hi"}]}]}'

# Via OpenClaw CLI
openclaw agent --local --agent main -m "say hello" --json
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| Port 8080 already in use | `pkill -f gemini_cli_proxy.py` then restart |
| `GEMINI_API_KEY not set` | Check `~/.env` contains `GEMINI_API_KEY=...` |
| Garbled ANSI in response | Already handled: `NO_COLOR=1 TERM=dumb` + regex strip |
| `TerminalQuotaError` | Daily quota exhausted — wait until next day |
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
- AI 模型：Gemini 3.1 Flash Lite（透過 gemini-cli-proxy 路由）
- Discord bot `@clawclaw` 已串接，@mention 觸發，slash commands 已對 user `876772650872090657` 開放
- Hailo NPU 由 detect.py 獨立使用，與 OpenClaw 無資源衝突
- Dashboard 需 HTTPS secure context，建議用 SSH tunnel 或 `openclaw tui`
- SD 卡鏡像已備份至外接 SSD（`/RPi5-Clone/rpi5-clone.img`，117GB，2026-03-19）
