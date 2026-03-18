下面是可直接存成 `openclaw_init_checklist.md` 的內容。內容依照 OpenClaw 在 Raspberry Pi 上需要 Node 22+、可用 daemon/systemd 常駐，以及 Tailscale tags/ACL 與 Gemini rate-limit retry 的實務要點整理而成。 [sunfounder](https://www.sunfounder.com/blogs/news/how-to-run-openclaw-on-raspberry-pi-a-practical-setup-guide)

```md
# OpenClaw Init Checklist

適用場景：Raspberry Pi 5 + Tailscale + OpenClaw + Gemini Flash 3  
目標：把 RPi5 建成可長期運行、可遠端管理、權限收斂的 AI 助理節點。

---

## 0. 目標原則

- [ ] RPi5 只當控制節點，不在本機跑大型模型。
- [ ] OpenClaw 只用低權限帳號執行。
- [ ] Tailscale 只做私網入口，不直接暴露 OpenClaw 到公網。
- [ ] Gemini API 一定有 timeout、retry、backoff。
- [ ] 所有高風險動作都需要白名單或人工確認。

---

## 1. 硬體準備

- [ ] Raspberry Pi 5 8GB。
- [ ] 穩定電源，建議官方等級 USB-C PD 電源。
- [ ] 主動散熱器或風扇。
- [ ] 優先使用 SSD；若先用 microSD，至少預留足夠空間給 logs、cache、temp。
- [ ] 有線網路優先，避免長跑 agent 時被 Wi‑Fi 波動影響。

---

## 2. OS 初始化

- [ ] 安裝 Raspberry Pi OS 64-bit。
- [ ] 更新系統：
  ```bash
  sudo apt update && sudo apt full-upgrade -y
  sudo reboot
  ```
- [ ] 設定正確時區與 locale。
- [ ] 啟用基本工具：
  ```bash
  sudo apt install -y curl git jq vim ca-certificates
  ```
- [ ] 建立一般管理帳號，不直接用預設帳號做所有事情。
- [ ] 關閉不必要服務，減少背景負載。

---

## 3. 帳號與 SSH

- [ ] 建立管理者帳號，例如：
  ```bash
  sudo adduser adminops
  sudo usermod -aG sudo adminops
  ```
- [ ] 匯入 SSH public key。
- [ ] 驗證 key-based SSH 可登入後，再考慮停用密碼登入。
- [ ] 不讓 OpenClaw 跑在管理者帳號底下。

---

## 4. 建立 OpenClaw 專用帳號

- [ ] 建立服務帳號：
  ```bash
  sudo useradd -r -m -d /var/lib/openclaw -s /usr/sbin/nologin openclaw
  ```
- [ ] 建立目錄：
  ```bash
  sudo mkdir -p /opt/openclaw /etc/openclaw /var/log/openclaw /srv/openclaw-work
  sudo chown -R openclaw:openclaw /opt/openclaw /etc/openclaw /var/log/openclaw /srv/openclaw-work /var/lib/openclaw
  ```
- [ ] 確認 `openclaw` 帳號沒有 sudo 免密碼權限。
- [ ] 所有 agent 工作都限制在指定工作目錄內。

---

## 5. 安裝 Node.js

OpenClaw 文件要求 Node 22+，新文件也將 Node 24 視為建議 runtime。

- [ ] 安裝 Node 22 或 24：
  ```bash
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt install -y nodejs
  ```
- [ ] 驗證版本：
  ```bash
  node --version
  npm --version
  ```
- [ ] 確認版本符合需求，至少 Node 22+。

---

## 6. 安裝 OpenClaw

- [ ] 先閱讀目前官方 Raspberry Pi / install 文件。
- [ ] 使用官方安裝方式安裝 OpenClaw。
- [ ] 若採官方 installer，先在互動模式測一次，再轉 daemon。
- [ ] 不要在 root 的互動 shell 裡長期跑 OpenClaw。

可先做一次基本安裝驗證：

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
openclaw --help
```

若採 onboarding / daemon 安裝：

```bash
openclaw onboard --install-daemon
```

- [ ] 完成後確認可啟動 gateway。
- [ ] 初期先不要加太多 channel，先用最小功能驗證。

---

## 7. OpenClaw 環境變數

- [ ] 建立環境檔：
  ```bash
  sudo nano /etc/openclaw/openclaw.env
  ```
- [ ] 權限收斂：
  ```bash
  sudo chown openclaw:openclaw /etc/openclaw/openclaw.env
  sudo chmod 600 /etc/openclaw/openclaw.env
  ```
- [ ] 將 Gemini API key 放在這裡，不寫死在 shell history 或 repo。

範例：

```env
NODE_ENV=production
PORT=3000
OPENCLAW_DATA_DIR=/var/lib/openclaw
OPENCLAW_WORK_DIR=/srv/openclaw-work
OPENCLAW_LOG_DIR=/var/log/openclaw

GEMINI_API_KEY=replace_me
GEMINI_MODEL=gemini-flash-3

OPENCLAW_MAX_CONCURRENCY=2
OPENCLAW_REQUEST_TIMEOUT_MS=90000
OPENCLAW_CONFIRM_DESTRUCTIVE=true
```

- [ ] 不把 SSH key、家控 token、雲端 API key 全部混在同一個 agent 可自由讀取的位置。
- [ ] 若有不同用途，分拆成多個 secrets 檔或不同服務帳號。

---

## 8. systemd 服務

- [ ] 建立 service：
  ```bash
  sudo nano /etc/systemd/system/openclaw.service
  ```

範例：

```ini
[Unit]
Description=OpenClaw Gateway
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=openclaw
Group=openclaw
WorkingDirectory=/opt/openclaw
EnvironmentFile=/etc/openclaw/openclaw.env
ExecStart=/usr/local/bin/openclaw gateway start
Restart=always
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

- [ ] 重新載入並啟用：
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now openclaw
  ```
- [ ] 檢查狀態：
  ```bash
  systemctl status openclaw
  journalctl -u openclaw -n 100 --no-pager
  ```
- [ ] 做一次 reboot test：
  ```bash
  sudo reboot
  ```

---

## 9. Tailscale 初始化

- [ ] 確認 `tailscaled` 正在運行：
  ```bash
  systemctl status tailscaled
  tailscale status
  ```
- [ ] 這台 Pi 先只做一般節點，不開 exit node。
- [ ] 這台 Pi 先不開 subnet router。
- [ ] 確認只透過 tailnet 存取管理面。
- [ ] 在 Tailscale admin console 替這台機器加 tag，例如 `tag:openclaw`。
- [ ] 不把這台 Pi 綁成個人身份用途的機器，長期應以 tag 管理。
- [ ] 視需求決定是否停用 key expiry；若停用，務必搭配 ACL。

---

## 10. Tailscale ACL 最小原則

- [ ] 只允許你自己的帳號或你的管理裝置連這台 Pi。
- [ ] 只開必要 port，例如 SSH 與 OpenClaw UI。
- [ ] 不要讓整個 tailnet 都能打到這台節點。
- [ ] 使用 tags 管理非人類機器。

最小概念範例：

```json
{
  "tagOwners": {
    "tag:openclaw": ["you@example.com"]
  },
  "acls": [
    {
      "action": "accept",
      "src": ["you@example.com"],
      "dst": ["tag:openclaw:22,3000"]
    }
  ]
}
```

- [ ] 在正式套用前先做 policy 測試。
- [ ] 留下文件說明每個 tag 的用途，避免之後 ACL 失控。

---

## 11. Gemini Flash 3 連線與保護

- [ ] 確認模型名稱與專案權限正確。
- [ ] 所有 API 呼叫都要有 timeout。
- [ ] 所有 API 呼叫都要有 retry。
- [ ] 遇到 `429` 或 `RESOURCE_EXHAUSTED` 要做 exponential backoff。
- [ ] 設定最大重試次數，避免無限重試卡死。

實作原則：

- 初始延遲：1 秒
- Backoff：2 倍成長
- 加 jitter
- 最大重試：3 到 5 次
- 超時後任務要能取消或回報失敗

---

## 12. 指令與工具安全

- [ ] 不允許 agent 直接執行任意 shell。
- [ ] 建立 wrapper scripts，只暴露白名單指令。
- [ ] 高風險動作需要人工確認，例如：
  - 刪除檔案
  - 改 system config
  - 安裝套件
  - 重新開機
  - 關閉服務
  - 對其他主機 SSH
- [ ] 若要控制家中設備，優先走專用 API，不要直接把 root shell 當萬用工具。

---

## 13. Logs 與監控

- [ ] 確認 OpenClaw logs 有固定路徑。
- [ ] 設定 journald 或 logrotate 限額。
- [ ] 定期檢查磁碟空間：
  ```bash
  df -h
  ```
- [ ] 監控記憶體與 CPU：
  ```bash
  free -h
  top
  ```
- [ ] 觀察 agent 任務是否長期堆積。
- [ ] 若有需求，可加 watchdog 或外部 health check。

---

## 14. 功能驗證

- [ ] 本機可正常啟動 OpenClaw。
- [ ] 透過 Tailscale IP 可從你的管理裝置連入。
- [ ] OpenClaw UI 不可從公網直接存取。
- [ ] Gemini API key 正常可用。
- [ ] 模擬 API 限流時，任務會重試而不是直接崩潰。
- [ ] 重開機後 OpenClaw 會自動恢復。
- [ ] 網路短暫中斷後可恢復。
- [ ] 日誌不會快速灌爆磁碟。
- [ ] 非授權裝置無法透過 tailnet 進入這台 Pi。

---

## 15. 上線前最後確認

- [ ] 已備份 `/etc/openclaw/`、systemd service、ACL policy。
- [ ] 已記錄 Tailscale machine name、tag、tailnet IP。
- [ ] 已記錄 OpenClaw 版本、Node 版本、OS 版本。
- [ ] 已確認所有 secrets 沒出現在 shell history、Git repo、公開筆記。
- [ ] 已確認沒有開 exit node / subnet router。
- [ ] 已確認 OpenClaw 不是以 root 執行。
- [ ] 已確認至少一種回滾方案可用。

---

## 16. 建議回滾方案

- [ ] 保留一份乾淨的 SD/SSD 映像。
- [ ] 保留 `/etc/systemd/system/openclaw.service` 備份。
- [ ] 保留 `/etc/openclaw/openclaw.env` 脫敏副本。
- [ ] 若升級 OpenClaw 或 Node，先記錄目前可用版本再動手。
- [ ] 每次只改一個變因，改完就驗證。

---

## 17. 常用檢查命令

```bash
node --version
npm --version
tailscale status
tailscale ip -4
systemctl status tailscaled
systemctl status openclaw
journalctl -u openclaw -n 100 --no-pager
ss -lntp
df -h
free -h
```

---

## 18. 最小可用基線

- OS: Raspberry Pi OS 64-bit
- Node: 22+，建議較新受支援版本
- OpenClaw: systemd 常駐
- Account: `openclaw` 低權限服務帳號
- Network: Tailscale only
- ACL: 僅允許你的裝置/帳號
- Model: Gemini Flash 3
- Safety: timeout + retry + backoff + destructive confirm
```

這份 `openclaw_init_checklist.md` 已經可以直接存檔使用，裡面的 OpenClaw 安裝、Node 版本、systemd、Tailscale tags/ACL 與 Gemini retry 原則都對應到目前文件與實務建議。 [tailscale](https://tailscale.com/docs/features/tags)

我也可以接著幫你產出兩個可直接貼上的檔案版本：`openclaw.service` 和 `tailscale_acl.json`。