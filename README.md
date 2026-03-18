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

## 常用維運指令

```bash
# OpenClaw
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

## 備註

- OpenClaw gateway 只綁定在 Tailscale IP，不對公網開放
- AI 模型：Gemini 2.0 Flash（視需要可改為 Claude API）
- Hailo NPU 由 detect.py 獨立使用，與 OpenClaw 無資源衝突
