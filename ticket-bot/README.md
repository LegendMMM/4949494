# Ticket Bot

這個專案是 Python 票務自動化工具，支援：

- `tixcraft`
- `kktix`
- `vieshow`
- `Ticketmaster` 關鍵字監控

目前專案同時提供：

- CLI 指令
- VieShow 專用 Web UI
- Telegram / Discord Bot

VieShow 的圖形介面說明請看 [README_VIESHOW.md](./README_VIESHOW.md)。

## 快速開始

### 1. 建立虛擬環境

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 準備設定檔

```powershell
Copy-Item config.yaml.example config.yaml
Copy-Item .env.example .env
```

或 macOS / Linux:

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

### 3. 瀏覽器說明

專案支援兩種引擎：

- `nodriver`
- `playwright`

目前程式已經加入 fallback 機制：

- 如果 `nodriver` 在你的 Python 環境無法匯入，會自動改用 `Playwright`
- 如果 `Playwright` 沒指定瀏覽器執行檔，會優先嘗試使用本機已安裝的 `Chrome` 或 `Edge`

如果你的電腦沒有可用的 Chrome / Chromium，再執行：

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

或：

```bash
playwright install chromium
```

## 你現在可以怎麼用

### 1. 最簡單的方式

直接雙擊：

```text
start_web.bat
```

或在 PowerShell 執行：

```powershell
.\start_web.bat
```

### 2. 直接用 Python 啟動 VieShow Web UI

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web
```

開啟：

```text
http://127.0.0.1:5000
```

這是目前最直覺的使用方式，適合威秀影城。

### 3. 手動登入平台

```powershell
.\.venv\Scripts\python.exe -m ticket_bot login --platform vieshow
```

也可以改成：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot login --platform tixcraft
.\.venv\Scripts\python.exe -m ticket_bot login --platform kktix
```

登入完成後回到終端按 Enter，程式會關閉瀏覽器並保留登入資料在 `browser.user_data_dir`。

### 4. 直接用 CLI 執行

```powershell
.\.venv\Scripts\python.exe -m ticket_bot run
```

常見變體：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot run --event 關鍵字
.\.venv\Scripts\python.exe -m ticket_bot run --date 2026/06/13 --area A區 --count 2
.\.venv\Scripts\python.exe -m ticket_bot run --parallel
.\.venv\Scripts\python.exe -m ticket_bot run --api
```

### 5. 監看釋票

```powershell
.\.venv\Scripts\python.exe -m ticket_bot watch --interval 5
```

### 6. 列出場次

```powershell
.\.venv\Scripts\python.exe -m ticket_bot list
```

### 7. 查看所有指令

```powershell
.\.venv\Scripts\python.exe -m ticket_bot --help
```

## CLI 指令總覽

```text
bot        啟動 Bot
countdown  倒數計時模式
label      標註驗證碼資料
list       列出場次
login      開啟登入頁
monitor    Ticketmaster 關鍵字監控
prepare    整理訓練資料
run        啟動搶票
watch      釋票監測
web        啟動 VieShow Web UI
```

## 最小設定範例

`config.yaml`:

```yaml
events:
  - name: "示例活動"
    platform: tixcraft
    url: "https://tixcraft.com/activity/game/your_event"
    ticket_count: 2
    date_keyword: ""
    area_keyword: ""
    sale_time: ""
    presale_code: ""

browser:
  engine: nodriver
  headless: false
  user_data_dir: "./chrome_profile"
  pre_warm: true
  lang: "zh-TW"
  executable_path: ""
  api_mode: "off"

vieshow:
  theater_code: ""
  theater_keyword: ""
  movie_keyword: ""
  showtime_keyword: ""
  ticket_type: "full"
  seat_preference: "center"
  ishow_email: ""
  ishow_password: ""
  auto_login: true
```

`.env`:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_BOT_TOKEN=
DISCORD_WEBHOOK_URL=
TICKETMASTER_API_KEY=
VIESHOW_ISHOW_EMAIL=
VIESHOW_ISHOW_PASSWORD=
```

## 環境需求

- Python `>= 3.11`
- Windows / macOS / Linux
- Chrome / Chromium / Edge 其一

## 目前已知行為

- 在某些 `Python 3.14` 環境下，`nodriver` 可能因第三方套件編碼問題無法匯入
- 專案目前已對這個情況做 fallback，會自動切到 `Playwright`
- 如果 `Playwright` 也找不到瀏覽器執行檔，請安裝 Chromium 或在 `browser.executable_path` 指定路徑

## 常見問題

### Web UI 打得開，但按開始後沒反應

先看右側 log。

如果看到瀏覽器相關錯誤，優先檢查：

- 本機是否有 `Chrome` / `Edge`
- `config.yaml` 的 `browser.executable_path` 是否正確
- 是否需要執行 `playwright install chromium`

### 我在 Windows 上要用哪條指令

最穩定的是：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web
```

### VieShow 要用哪份說明

請直接看 [README_VIESHOW.md](./README_VIESHOW.md)。

## 專案結構

```text
src/ticket_bot/
  browser/
    base.py
    factory.py
    nodriver_engine.py
    playwright_engine.py
  platforms/
    kktix.py
    ticketmaster.py
    tixcraft.py
    tixcraft_api.py
    vieshow.py
    vieshow_parser.py
  web/
    app.py
    templates/index.html
  cli.py
  config.py

tests/
README.md
README_VIESHOW.md
```

## License

MIT。詳見 `LICENSE`。
