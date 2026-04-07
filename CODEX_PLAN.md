# 威秀搶票機器人 — 反偵測重構方案

> 本文件供 Codex 實作。完整描述「為什麼現在被擋」與「怎麼改」。

---

## 一、現有方案為何被擋

### 1.1 瀏覽器指紋層（被偵測機率：高）

| 問題 | 現狀 | 為何被擋 |
|------|------|----------|
| NoDriver 啟動新 Chrome | `uc.start()` 建立全新 profile | Cloudflare 看到全新指紋（無 cookie 歷史、無 localStorage） |
| Stealth JS 注入時機 | `add_script_to_evaluate_on_new_document` | 注入本身會留下 CDP side-effect（`Runtime.evaluate` 痕跡） |
| WebGL 硬編碼 Intel | `getParameter` override 回傳 "Intel Inc." | 若使用者實際是 NVIDIA 顯卡，指紋前後矛盾 |
| `--disable-blink-features=AutomationControlled` | 已加 | Cloudflare 2024+ 已不只看這個 flag |

### 1.2 行為指紋層（被偵測機率：極高）

| 問題 | 現狀 | 為何被擋 |
|------|------|----------|
| 零滑鼠軌跡 | 直接 `element.click()` | 無 mousemove 事件，伺服器端 JS 可蒐集 |
| 零捲動 | 只用 JS evaluate，無 scroll 事件 | 真人一定會捲動頁面 |
| 動作間隔固定 | `await asyncio.sleep(1.0)` 或 `sleep(0.3)` | 機器化等距 pattern |
| 整流程 < 3 秒 | 選影城 → 選電影 → 選座 → 結帳全部用 JS 直填 | 人類不可能這麼快完成 |
| 無 focus/blur 事件 | 不模擬 tab 切換、視窗失焦 | 真實使用者常會切分頁 |

### 1.3 網路層（被偵測機率：中高）

| 問題 | 現狀 | 為何被擋 |
|------|------|----------|
| 封鎖追蹤資源 | `block_urls()` 擋了 GA、FB pixel 等 | 伺服器發現這些請求完全沒出現，反而更可疑 |
| `executable_path` 預設 Linux 路徑 | `/usr/bin/chromium` | Windows 上 FileNotFoundError，bot 直接啟動失敗 |
| 無 Referer header | 導航時缺少正確 Referer | 伺服器端驗證 Referer chain |

### 1.4 Cloudflare Turnstile / JS Challenge

- 現有的 `verify_cf()` 使用模板匹配點擊 checkbox，對 Managed Challenge 無效
- Cloudflare 的 Managed Challenge 會分析整個瀏覽器 session 的行為分數，不是只點一個 checkbox

---

## 二、新方案架構：「接管真實瀏覽器」

### 核心思路

**不再啟動新的自動化瀏覽器，而是接管使用者已經開啟的 Chrome。**

理由：
1. 使用者的 Chrome 有完整的 cookie 歷史、localStorage、真實硬體指紋 → Cloudflare 信任分數高
2. 不需要注入 stealth JS → 沒有 CDP 痕跡
3. 使用者可以先手動過 Cloudflare challenge → bot 只接管後續流程

### 架構圖

```
┌─────────────────────────────────────────────────┐
│  使用者的 Chrome（手動開啟，帶 --remote-debugging-port）│
│                                                   │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ 威秀分頁  │   │ 其他分頁  │   │ Web UI   │     │
│  │(使用者先  │   │          │   │ :5000    │     │
│  │ 打開+登入)│   │          │   │          │     │
│  └──────────┘   └──────────┘   └──────────┘     │
│        ▲                              ▲          │
└────────┼──────────────────────────────┼──────────┘
         │ CDP (ws://127.0.0.1:9222)    │ HTTP
         │                              │
┌────────┴──────────────────────────────┴──────────┐
│              ticket-bot 後端 (Python)              │
│                                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │ CDP Takeover │  │ Human Timing │  │ Web UI   │ │
│  │ Engine       │  │ Simulator    │  │ Flask    │ │
│  └─────────────┘  └──────────────┘  └──────────┘ │
│         │                                          │
│  ┌──────┴──────────────────────────────────────┐  │
│  │        State Machine (簡化版)                │  │
│  │  wait_for_seat → click_seats → confirm      │  │
│  └─────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

---

## 三、具體實作項目

### 3.1 Chrome 接管模式（取代 NoDriver 啟動）

**檔案：`src/ticket_bot/browser/cdp_takeover.py`（新建）**

```python
"""
CDP Takeover Engine — 接管使用者已開啟的 Chrome。
不啟動新 Chrome，不注入 stealth JS。
"""

import websockets
import json
import asyncio

class CDPTakeoverEngine:
    """透過 CDP WebSocket 接管使用者的 Chrome"""

    async def connect(self, cdp_url: str = "http://127.0.0.1:9222"):
        """
        連線到使用者的 Chrome Remote Debugging。
        使用者需要用以下方式啟動 Chrome：
          chrome.exe --remote-debugging-port=9222
        """
        # 1. GET http://127.0.0.1:9222/json → 取得所有分頁
        # 2. 找到包含 "vscinemas.com.tw" 的分頁
        # 3. 用 WebSocket 連線到該分頁的 webSocketDebuggerUrl
        pass

    async def evaluate(self, expression: str):
        """直接用 CDP Runtime.evaluate 執行 JS"""
        pass

    async def click_element(self, selector: str):
        """
        模擬真實點擊（非 element.click()）：
        1. 用 DOM.querySelector 找到元素
        2. 用 DOM.getBoxModel 取得座標
        3. 用 Input.dispatchMouseEvent 發送 mousemove → mousedown → mouseup → click
        """
        pass

    async def human_mouse_move(self, from_xy, to_xy, duration_ms=300):
        """
        Bézier 曲線滑鼠移動：
        - 產生 10-20 個中間點
        - 每個點之間 15-30ms
        - 加上微小的隨機偏移（±2px）
        """
        pass
```

**關鍵設計：**
- 不呼叫 `uc.start()`，不建立新 profile
- 不注入任何 stealth JS（使用者的 Chrome 本來就不需要）
- 所有 JS eval 只用於「讀取 DOM 狀態」，不用於「觸發操作」
- 所有操作改用 `Input.dispatchMouseEvent` / `Input.dispatchKeyEvent`

### 3.2 人類行為模擬器

**檔案：`src/ticket_bot/human/timing.py`（新建）**

```python
"""
模擬人類操作的時間特徵。
所有等待時間基於真實使用者行為研究的分布。
"""
import random
import math

def think_delay() -> float:
    """模擬人類「看到頁面 → 決定下一步」的思考時間"""
    # 對數常態分布，中位數 0.8 秒，右尾可到 2-3 秒
    return random.lognormvariate(math.log(0.8), 0.4)

def click_delay() -> float:
    """模擬人類「決定點擊 → 實際點擊」的反應時間"""
    # 常態分布，均值 200ms，標準差 50ms
    return max(0.1, random.gauss(0.2, 0.05))

def scroll_pattern() -> list[dict]:
    """
    產生一組自然的捲動事件序列。
    真實使用者的捲動特徵：
    - 快速滑動 → 慢下來 → 停頓 → 再滑
    - 偶爾往回捲一點點
    """
    pass

def typing_delays(text: str) -> list[float]:
    """
    模擬打字速度。
    - 平均 WPM 60-80
    - 常見字母組合（th, er, ing）打得快
    - 偶爾暫停（回想下一個字）
    """
    pass
```

### 3.3 簡化的狀態機（取代 300-step loop）

**修改：`src/ticket_bot/platforms/vieshow.py`**

把現有的「全自動 300 步 loop」改成「使用者主導 + bot 只在關鍵瞬間接管」：

```
使用者手動完成：
  ✓ 打開 Chrome（帶 --remote-debugging-port=9222）
  ✓ 登入 iShow 帳號
  ✓ 過 Cloudflare challenge
  ✓ 導航到選座頁面

Bot 接管的部分（精簡到三步）：
  1. 【等待 + 監控】偵測到座位圖載入
  2. 【搶座】用 CDP dispatchMouseEvent 點擊最佳座位
  3. 【確認】偵測到票種頁 → 選票種 → 確認送出
```

**新的 run 流程：**

```python
async def run_takeover(self) -> bool:
    """接管模式：只做搶座 + 確認，其餘讓使用者手動"""

    # 1. 連線到使用者的 Chrome
    await self.cdp.connect("http://127.0.0.1:9222")
    self._report("已連線到 Chrome")

    # 2. 等待使用者導航到正確頁面
    self._report("請在 Chrome 中打開威秀訂票頁面，選好影城和電影...")
    while True:
        url = await self.cdp.get_current_url()
        if "vscinemas.com.tw" in url:
            break
        await asyncio.sleep(1.0)

    # 3. 監控頁面狀態，偵測到座位圖就開始搶
    self._report("監控中... 偵測到座位圖時自動搶座")
    while not self._stop_requested:
        state = await self._detect_state_minimal()

        if state == "seat_selection":
            await asyncio.sleep(think_delay())  # 模擬思考
            result = await self._grab_seats_human()
            if result:
                self._report(f"已選座: {result}")
                # 等待跳轉到票種頁
                await asyncio.sleep(think_delay())
                await self._confirm_ticket_type_human()
                return True

        elif state == "checkout":
            self._report("已進入結帳頁！請手動完成付款")
            return True

        await asyncio.sleep(0.5 + random.uniform(0, 0.3))
```

### 3.4 搶座邏輯重寫：用 CDP Input 取代 JS click

**現有問題：** `SEAT_SELECT_JS` 用 `element.click()` 觸發，伺服器端 JS 可以分辨程式化的 `click()` 和真實的滑鼠事件（`isTrusted` property）。

**修改重點：**

```python
async def _grab_seats_human(self):
    """用 CDP Input events 模擬真實滑鼠點擊座位"""

    # 1. 用 JS 讀取座位資訊（只讀，不操作）
    seats_info = await self.cdp.evaluate("""
        (() => {
            const seats = document.querySelectorAll('[class*="seat"]:not([class*="occupied"])');
            return Array.from(seats).map(s => {
                const r = s.getBoundingClientRect();
                return {
                    id: s.id || s.dataset.id || '',
                    x: r.left + r.width/2,
                    y: r.top + r.height/2,
                    row: s.dataset.row || '',
                    col: s.dataset.col || ''
                };
            });
        })()
    """)

    # 2. 選出最佳座位（偏好邏輯不變）
    best = self._pick_best_seats(seats_info, count=self.event.ticket_count)

    # 3. 逐一用滑鼠點擊
    for seat in best:
        # 先移動滑鼠到座位位置（Bézier 曲線）
        await self.cdp.human_mouse_move(
            from_xy=self._last_mouse_pos,
            to_xy=(seat['x'], seat['y']),
            duration_ms=random.randint(200, 400)
        )
        # 等一下再點（模擬確認是不是這個座位）
        await asyncio.sleep(click_delay())
        # CDP Input.dispatchMouseEvent
        await self.cdp.dispatch_click(seat['x'], seat['y'])
        self._last_mouse_pos = (seat['x'], seat['y'])
        # 座位之間的停頓
        await asyncio.sleep(think_delay() * 0.5)

    return [s['id'] for s in best]
```

### 3.5 不要封鎖追蹤資源

**刪除：** `BLOCKED_URL_PATTERNS` 和 `block_urls()` 的呼叫。

封鎖 GA / FB pixel 不但不能幫助你避開偵測，反而讓你更可疑——正常使用者的瀏覽器一定會載入這些資源。伺服器端如果發現某個 session 完全沒有 GA beacon，就知道這不是正常瀏覽器。

### 3.6 Web UI 更新

**修改：`src/ticket_bot/web/app.py` 和 `templates/index.html`**

新增「接管模式」的 UI：

```
┌──────────────────────────────────────────────┐
│  1. 開啟 Chrome（請用桌面上的 start_chrome.bat）│
│  2. 在 Chrome 中登入 iShow 帳號               │
│  3. 選好影城和電影                             │
│  4. 按下「開始監控」                            │
│                                                │
│  [  開始監控  ]                                 │
│                                                │
│  狀態：等待連線到 Chrome...                     │
│  > 已連線                                       │
│  > 偵測到威秀訂票頁                             │
│  > 監控中...等待座位圖載入                      │
│  > ★ 偵測到座位！正在搶座...                    │
│  > 已選座：F10, F11                             │
│  > 已選票種：全票 x 2                           │
│  > 進入結帳頁 — 請手動完成付款！                │
└──────────────────────────────────────────────┘
```

### 3.7 Chrome 啟動腳本

**檔案：`start_chrome.bat`（新建）**

```bat
@echo off
:: 啟動 Chrome 並開啟 Remote Debugging
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data" ^
    https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx
echo Chrome 已啟動（遠端除錯模式）
echo 請在 Chrome 中登入並選好電影，然後回到 Web UI 按「開始監控」
pause
```

這個 bat 做的事：
- 用使用者自己的 Chrome profile（有完整 cookie 歷史）
- 開啟 remote debugging port 9222
- 直接導航到威秀訂票頁

---

## 四、要刪掉 / 不再使用的東西

| 項目 | 原因 |
|------|------|
| `BLOCKED_URL_PATTERNS` | 封鎖追蹤資源反而更可疑 |
| `STEALTH_JS` 整段 | 接管真實 Chrome 不需要 stealth |
| `nodriver_engine.py` 的 `launch()` | 不再啟動新 Chrome |
| `DETECT_STATE_JS` 的三份重複定義 | 合併為一份，只做唯讀偵測 |
| `SEAT_SELECT_JS` 裡的 `element.click()` | 改用 CDP dispatchMouseEvent |
| `executable_path = "/usr/bin/chromium"` | 已修為空字串，但新方案不需要 |
| `api_stop` 不設 `running=False` | 已修正 |

---

## 五、檔案清單與優先順序

### Phase 1（核心 — 必做）

| 優先 | 檔案 | 動作 |
|------|------|------|
| P0 | `src/ticket_bot/browser/cdp_takeover.py` | **新建**：CDP WebSocket 接管引擎 |
| P0 | `src/ticket_bot/human/timing.py` | **新建**：人類行為時間模擬器 |
| P0 | `src/ticket_bot/human/__init__.py` | **新建**：模組 init |
| P0 | `src/ticket_bot/platforms/vieshow.py` | **重寫** `run()` 為 `run_takeover()` |
| P0 | `start_chrome.bat` | **新建**：使用者啟動 Chrome 的腳本 |

### Phase 2（Web UI 整合）

| 優先 | 檔案 | 動作 |
|------|------|------|
| P1 | `src/ticket_bot/web/app.py` | **修改**：新增接管模式路由 |
| P1 | `src/ticket_bot/web/templates/index.html` | **修改**：新增接管模式 UI |
| P1 | `src/ticket_bot/cli.py` | **修改**：新增 `ticket-bot takeover` 命令 |

### Phase 3（清理）

| 優先 | 檔案 | 動作 |
|------|------|------|
| P2 | `src/ticket_bot/browser/nodriver_engine.py` | 保留但標記 deprecated |
| P2 | `src/ticket_bot/platforms/vieshow.py` | 清除重複的 `DETECT_STATE_JS` |
| P2 | `src/ticket_bot/config.py` | 新增 `TakeoverConfig` dataclass |
| P2 | `tests/` | 新增接管模式的測試 |

---

## 六、CDP dispatchMouseEvent 完整範例

這是 Codex 最需要的核心實作參考：

```python
import json
import asyncio
import math
import random
import aiohttp

class CDPConnection:
    def __init__(self):
        self.ws = None
        self._msg_id = 0

    async def connect(self, cdp_url="http://127.0.0.1:9222"):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{cdp_url}/json") as resp:
                tabs = await resp.json()

        # 找到威秀的分頁
        target = None
        for tab in tabs:
            if "vscinemas" in tab.get("url", ""):
                target = tab
                break
        if not target:
            raise RuntimeError("找不到威秀的分頁，請先在 Chrome 打開威秀網站")

        self.ws = await websockets.connect(target["webSocketDebuggerUrl"])

    async def send(self, method: str, params: dict = None) -> dict:
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(msg))
        while True:
            resp = json.loads(await self.ws.recv())
            if resp.get("id") == self._msg_id:
                return resp.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        return result.get("result", {}).get("value")

    async def dispatch_mouse(self, event_type: str, x: float, y: float, button="left"):
        await self.send("Input.dispatchMouseEvent", {
            "type": event_type,
            "x": x, "y": y,
            "button": button,
            "clickCount": 1 if event_type == "mousePressed" else 0,
        })

    async def human_click(self, x: float, y: float):
        """完整的人類點擊序列"""
        # 1. mouseMoved（靠近目標）
        await self.dispatch_mouse("mouseMoved", x - 2, y - 1)
        await asyncio.sleep(random.uniform(0.01, 0.03))
        await self.dispatch_mouse("mouseMoved", x, y)
        await asyncio.sleep(random.uniform(0.02, 0.05))
        # 2. mousePressed
        await self.dispatch_mouse("mousePressed", x, y, "left")
        await asyncio.sleep(random.uniform(0.05, 0.12))
        # 3. mouseReleased
        await self.dispatch_mouse("mouseReleased", x, y, "left")

    async def bezier_move(self, from_xy, to_xy, steps=15, duration_ms=300):
        """Bézier 曲線滑鼠移動"""
        x0, y0 = from_xy
        x3, y3 = to_xy
        # 兩個控制點加隨機偏移
        dx = x3 - x0
        dy = y3 - y0
        x1 = x0 + dx * 0.3 + random.uniform(-30, 30)
        y1 = y0 + dy * 0.0 + random.uniform(-30, 30)
        x2 = x0 + dx * 0.7 + random.uniform(-20, 20)
        y2 = y0 + dy * 1.0 + random.uniform(-20, 20)

        step_delay = duration_ms / 1000 / steps
        for i in range(steps + 1):
            t = i / steps
            # cubic Bézier
            x = (1-t)**3*x0 + 3*(1-t)**2*t*x1 + 3*(1-t)*t**2*x2 + t**3*x3
            y = (1-t)**3*y0 + 3*(1-t)**2*t*y1 + 3*(1-t)*t**2*y2 + t**3*y3
            # 加入微小抖動
            x += random.uniform(-1, 1)
            y += random.uniform(-1, 1)
            await self.dispatch_mouse("mouseMoved", x, y)
            await asyncio.sleep(step_delay + random.uniform(-0.005, 0.005))
```

---

## 七、依賴變更

### 新增

```toml
# pyproject.toml 新增
"websockets>=12.0",
"aiohttp>=3.9",
```

### 可移除（Phase 3）

```
nodriver  # 接管模式不需要
playwright  # 接管模式不需要
```

---

## 八、使用者操作流程（最終目標）

```
1. 雙擊 start_chrome.bat
   → Chrome 開啟威秀訂票頁（帶 remote debugging）

2. 在 Chrome 中：
   → 登入 iShow 帳號（如果需要）
   → 過 Cloudflare（如果跳出來，手動點就好）
   → 選好影城、電影、場次
   → 停在「選座位」之前的頁面

3. 雙擊 restart.bat（或 diagnose_and_run.bat）
   → 啟動 Web UI

4. 在 Web UI (http://127.0.0.1:5000)：
   → 設定票數、票種、座位偏好
   → 按「開始監控」

5. Bot 自動：
   → 連線到 Chrome
   → 等待座位圖載入
   → 用擬人滑鼠動作搶座
   → 選票種
   → 提示使用者手動付款
```

---

## 九、為什麼這個方案不會被擋

| 偵測向量 | 舊方案 | 新方案 |
|----------|--------|--------|
| 瀏覽器指紋 | 全新 NoDriver Chrome | 使用者自己的 Chrome（完整歷史） |
| Cloudflare 信任分 | 0（新 session） | 高（有 cookie 歷史） |
| CDP 痕跡 | stealth JS 試圖掩蓋 | 不需要掩蓋（真實 Chrome） |
| 滑鼠軌跡 | 無（JS click） | Bézier 曲線擬人移動 |
| 點擊事件 | `isTrusted: false` | `isTrusted: true`（Input.dispatch） |
| 操作速度 | < 3 秒完成全流程 | 對數常態分布延遲 |
| 追蹤資源 | 全部封鎖 | 正常載入 |
| 登入/CF challenge | bot 嘗試自動處理（常失敗） | 使用者手動處理（100% 成功） |

---

## 十、注意事項

1. **`Input.dispatchMouseEvent` 產生的事件 `isTrusted` 為 `true`** — 這是 CDP 的特性，和 JS `element.click()` 不同
2. **`--remote-debugging-port` 必須在 Chrome 啟動時指定**，不能事後開啟
3. **如果使用者的 Chrome 已經在跑（沒帶 debugging port）**，需要先關掉再用 bat 重開
4. **start_chrome.bat 要自動偵測 Chrome 路徑**：先找 `Program Files`，再找 `Program Files (x86)`，再找 Registry
5. **websockets 和 aiohttp 要加到 pyproject.toml 的 dependencies**
6. **保留舊的 NoDriver 模式作為 fallback**，讓使用者可以在 config 裡選擇 `mode: "takeover"` 或 `mode: "auto"`
