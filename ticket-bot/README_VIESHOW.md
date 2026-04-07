# VieShow Web UI 使用說明

這份文件只講威秀影城的使用方式。

如果你只是想先跑起來，照下面做就可以。

## 一鍵理解

你現在最推薦的使用方式是直接執行：

```text
start_web.bat
```

或：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web
```

然後打開：

```text
http://127.0.0.1:5000
```

Web UI 會幫你啟動後端的 `VieShowBot`，並在畫面右側顯示即時 log。

## 啟動前準備

### 1. 安裝相依套件

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. 複製設定檔

```powershell
Copy-Item config.yaml.example config.yaml
Copy-Item .env.example .env
```

### 3. 確認本機有 Chrome

目前 Web UI 支援：

- `nodriver`
- `Playwright`

如果 `nodriver` 在你的環境壞掉，程式會自動 fallback 到 `Playwright`。  
如果 `Playwright` 沒有自己的瀏覽器，也會優先嘗試使用本機安裝的 Chrome。

如果你沒有 Chrome / Edge，可以安裝 Playwright 的 Chromium：

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

## 啟動方式

### 預設本機啟動

最省事：

```powershell
.\start_web.bat
```

手動指令：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web
```

### 指定連接埠

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web --port 5010
```

### 允許區網存取

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web --host 0.0.0.0 --port 5000
```

## Web UI 要怎麼填

### 影城代碼

例如：

- `TP`：信義威秀
- `MU`：MUVIE ONE
- `BQ`：板橋威秀

如果你不想記代碼，也可以填影城關鍵字。

### 電影關鍵字

輸入你要找的片名關鍵字，例如：

- `復仇者`
- `灌籃高手`
- `名偵探柯南`

### 場次關鍵字

可輸入：

- 時間，例如 `19:30`
- 格式，例如 `IMAX`
- 其他你想匹配的場次文字

### 票數

要買幾張票。

### 票種

目前介面提供：

- `full`
- `student`
- `ishow`
- `senior`
- `love`

### 座位偏好

可以填：

- `center`
- `front`
- `back`
- 明確座位，例如 `F12,F13`

### iShow 帳密

如果你希望自動登入，就填：

- `ishow_email`
- `ishow_password`

也可以放進 `.env`：

```env
VIESHOW_ISHOW_EMAIL=your_email@example.com
VIESHOW_ISHOW_PASSWORD=your_password
```

## 兩種模式

### 1. 直接執行

按下「直接執行」後，程式會立刻開始跑完整流程。

適合：

- 已經有場次
- 想直接進入選位 / 搶票流程

### 2. 持續監看

按下「持續監看」後，程式會定期刷新頁面，直到出現可搶的場次或座位。

適合：

- 等釋票
- 等新場次開出

## 建議使用流程

### 流程 A：先用 Web UI

1. 啟動 `python -m ticket_bot web`
2. 開 `http://127.0.0.1:5000`
3. 先填影城、電影、場次
4. 需要 iShow 就填帳密
5. 先試 `持續監看`
6. 看到右側 log 正常刷新後再長時間掛著

### 流程 B：先手動登入再回 Web UI

1. 執行：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot login --platform vieshow
```

2. 在瀏覽器完成登入
3. 回終端按 Enter
4. 再啟動 Web UI

這樣可以降低登入步驟出問題的機率。

## 常見排錯

### 1. Web UI 開得起來，但按開始後立刻失敗

先看右側 log。

重點檢查：

- 本機是否有 Chrome / Edge
- `browser.executable_path` 是否填錯
- 目標網站是否改版

### 2. `nodriver` 壞掉怎麼辦

目前程式已經會自動 fallback 到 `Playwright`，不需要你手動改 code。

如果還是不行，再補裝：

```powershell
.\.venv\Scripts\playwright.exe install chromium
```

### 3. 停止按鈕有沒有用

現在有用。  
Web UI 的 `stop` 會正確把停止要求送到背景 bot，狀態也會回到 `stopped`。

### 4. 可以遠端打開嗎

可以，用：

```powershell
.\.venv\Scripts\python.exe -m ticket_bot web --host 0.0.0.0 --port 5000
```

但這只是把服務綁到外部位址，不代表你應該直接裸露到公網。

## 相關檔案

```text
src/ticket_bot/web/app.py
src/ticket_bot/web/templates/index.html
src/ticket_bot/platforms/vieshow.py
src/ticket_bot/platforms/vieshow_parser.py
```

## 補充

如果你要的是整個專案的 CLI 用法，回到 [README.md](./README.md)。  
如果你要我再幫你把 VieShow 的預設 `config.yaml` 也一起整理成可直接跑的版本，我可以接著改。
