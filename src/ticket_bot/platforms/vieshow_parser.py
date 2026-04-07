"""威秀影城 HTML 解析器 — 解析影城、電影、場次、座位、票種頁面"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ── 影城代碼對照表 ──────────────────────────────────────────────
THEATER_CODES: dict[str, str] = {
    "TP": "信義威秀",
    "MU": "松仁MUVIE ONE",
    "NL": "南港威秀",
    "QS": "京站威秀",
    "TX": "天母威秀",
    "BQ": "板橋威秀",
    "GM": "大直威秀",
    "HU": "環球威秀",
    "LK": "林口���秀",
    "TY": "���園威秀",
    "TG": "Tiger City威秀",
    "HS": "新竹威秀",
    "BC": "頭份尚順威秀",
    "TF": "台中威秀",
    "TZ": "台南威秀",
    "TN": "台南南紡威秀",
    "FC": "大遠百威秀",
    "NF": "楠梓威秀",
    "KS": "高雄威秀",
    "HL": "花蓮威秀",
}

# 反向對照：名稱 → 代碼
THEATER_NAME_TO_CODE: dict[str, str] = {v: k for k, v in THEATER_CODES.items()}


def _clean_text(value: str) -> str:
    text = unescape(_TAG_RE.sub(" ", value))
    return _WS_RE.sub(" ", text).strip()


def _search(pattern: str, html: str, flags: int = 0) -> str:
    match = re.search(pattern, html, flags)
    return match.group(1) if match else ""


# ── 影城解析 ───────��────────────────────────────────────────────

class _TheaterOptionParser(HTMLParser):
    """解析 <select> 中的影城 <option> 列表"""

    def __init__(self):
        super().__init__()
        self.theaters: list[dict] = []
        self._in_select = False
        self._in_option = False
        self._option_value = ""
        self._option_selected = False
        self._option_text = ""

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "select":
            name = attr.get("name", "") + attr.get("id", "")
            if "cinema" in name.lower() or "theater" in name.lower() or "site" in name.lower():
                self._in_select = True
        elif tag == "option" and self._in_select:
            self._in_option = True
            self._option_value = attr.get("value", "")
            self._option_selected = "selected" in attr
            self._option_text = ""

    def handle_endtag(self, tag):
        if tag == "option" and self._in_option:
            self._in_option = False
            text = self._option_text.strip()
            if self._option_value and text:
                self.theaters.append({
                    "code": self._option_value,
                    "name": text,
                    "selected": self._option_selected,
                })
        elif tag == "select" and self._in_select:
            self._in_select = False

    def handle_data(self, data):
        if self._in_option:
            self._option_text += data


def parse_theater_list(html: str) -> list[dict]:
    """���析影城下拉選單，回傳 [{"code": "TP", "name": "信義威秀", "selected": bool}]"""
    parser = _TheaterOptionParser()
    parser.feed(html)
    if parser.theaters:
        return parser.theaters
    # 如果 HTML 解析失敗，回傳靜態列表
    return [{"code": code, "name": name, "selected": False} for code, name in THEATER_CODES.items()]


def matches_theater(text: str, keyword: str) -> bool:
    """比對影城：支援代碼（TP）或名稱關鍵字（信義）"""
    if not keyword:
        return False
    keyword = keyword.strip().upper()
    # 嘗試匹配代碼
    if keyword in THEATER_CODES:
        return keyword in text.upper() or THEATER_CODES[keyword] in text
    # 嘗試匹配名稱關鍵字
    keyword_lower = keyword.lower()
    return keyword_lower in text.lower()


# ── 電影列表解析 ────────────────────────────────────────────────

class _MovieListParser(HTMLParser):
    """解析電影列表頁面"""

    def __init__(self):
        super().__init__()
        self.movies: list[dict] = []
        self._in_movie = False
        self._movie_depth = 0
        self._current_movie: dict = {}
        self._in_title = False
        self._in_showtime = False
        self._current_text = ""
        self._in_a = False
        self._a_href = ""

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        cls = attr.get("class", "")

        if tag in ("div", "li") and ("movie" in cls or "film" in cls or "movieItem" in cls):
            self._in_movie = True
            self._movie_depth = 1
            self._current_movie = {"title": "", "showtimes": [], "id": attr.get("data-id", "")}
        elif self._in_movie:
            if tag == "div":
                self._movie_depth += 1
            if "title" in cls or "name" in cls:
                self._in_title = True
                self._current_text = ""
            elif "time" in cls or "showtime" in cls or "session" in cls:
                self._in_showtime = True
                self._current_text = ""
            elif tag == "a":
                self._in_a = True
                self._a_href = attr.get("href", "")
                self._current_text = ""

    def handle_endtag(self, tag):
        if self._in_movie:
            if tag == "div":
                self._movie_depth -= 1
                if self._movie_depth == 0:
                    if self._current_movie.get("title"):
                        self.movies.append(self._current_movie)
                    self._in_movie = False
                    self._current_movie = {}
            if self._in_title and tag in ("h2", "h3", "span", "div", "a"):
                self._current_movie["title"] = self._current_text.strip()
                self._in_title = False
            if self._in_showtime and tag in ("span", "div", "li", "a"):
                text = self._current_text.strip()
                if text:
                    self._current_movie.setdefault("showtimes", []).append({
                        "time": text,
                        "url": self._a_href if self._in_a else "",
                        "available": True,
                    })
                self._in_showtime = False
            if self._in_a and tag == "a":
                self._in_a = False
                self._a_href = ""

    def handle_data(self, data):
        if self._in_title or self._in_showtime or self._in_a:
            self._current_text += data


def parse_movie_list(html: str) -> list[dict]:
    """解析電影列表，回傳 [{"title": str, "showtimes": [...], "id": str}]"""
    parser = _MovieListParser()
    parser.feed(html)

    # 如果結構化解析失敗，嘗試 regex 提取
    if not parser.movies:
        movies = []
        # 嘗試找電影標題
        titles = re.findall(r'class="[^"]*(?:movie|film)[^"]*title[^"]*"[^>]*>([^<]+)', html, re.IGNORECASE)
        if not titles:
            titles = re.findall(r'<h[23][^>]*>([^<]{2,60})</h[23]>', html)
        for title in titles:
            movies.append({"title": _clean_text(title), "showtimes": [], "id": ""})
        return movies

    return parser.movies


# ── 座位圖解析 ───��──────────────────────────────────────────────

def parse_seat_map(html: str) -> dict:
    """
    解析座位圖 HTML，回傳 {
        "available": [{"row": "F", "number": 12, "id": "F12"}, ...],
        "occupied": [...],
        "total": int
    }
    """
    available: list[dict] = []
    occupied: list[dict] = []

    # 通用模式：找帶有座位資訊的元素
    seat_patterns = [
        # pattern: (regex, row_group, number_group)
        (r'data-row=["\'](\w+)["\'][^>]*data-(?:col|num|seat)=["\'](\d+)["\']', 1, 2),
        (r'id=["\']seat[_-]?(\w+?)[_-]?(\d+)["\']', 1, 2),
        (r'class=["\'][^"\']*seat[^"\']*["\'][^>]*data-id=["\'](\w)(\d+)["\']', 1, 2),
    ]

    found = False
    for pattern, rg, ng in seat_patterns:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            row = match.group(rg).upper()
            number = int(match.group(ng))
            seat_id = f"{row}{number}"

            # 判斷座位是否可選——限縮到包含 match 的單一標籤
            tag_open = html.rfind('<', 0, match.start())
            tag_close = html.find('>', match.end())
            if tag_open == -1:
                tag_open = max(0, match.start() - 200)
            if tag_close == -1:
                tag_close = min(len(html), match.end() + 100)
            context = html[tag_open:tag_close + 1]
            is_occupied = bool(re.search(
                r'(?:occupied|taken|sold|disabled|unavailable|selected|booked)',
                context, re.IGNORECASE
            ))

            seat = {"row": row, "number": number, "id": seat_id}
            if is_occupied:
                occupied.append(seat)
            else:
                available.append(seat)
            found = True

        if found:
            break

    return {
        "available": available,
        "occupied": occupied,
        "total": len(available) + len(occupied),
    }


# ── 票種解析 ────���────────────────────────────────���──────────────

def parse_ticket_types(html: str) -> list[dict]:
    """
    解析票種選擇，回傳 [{"name": str, "price": int, "code": str, "available": bool}]
    """
    types: list[dict] = []

    # 模式一：<option> 裡的票種
    for match in re.finditer(
        r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>([^<]+)',
        html,
    ):
        code = match.group(1)
        text = _clean_text(match.group(2))
        if not text or code == "":
            continue

        # 嘗試提取價格
        price_match = re.search(r'\$?\s*(\d[\d,]*)', text)
        price = int(price_match.group(1).replace(",", "")) if price_match else 0

        types.append({
            "name": text,
            "price": price,
            "code": code,
            "available": "disabled" not in match.group(0).lower(),
        })

    # 模式二：表格或 div 列表裡的票種
    if not types:
        for match in re.finditer(
            r'(?:全票|優待票|愛心票|敬老票|學生票|兒童票|會員票|iShow|儲值金)',
            html,
        ):
            start = max(0, match.start() - 100)
            context = html[start:match.end() + 200]
            name = match.group(0)
            price_match = re.search(r'(?:NT\$?|TWD)\s*(\d[\d,]*)', context)
            price = int(price_match.group(1).replace(",", "")) if price_match else 0
            types.append({
                "name": name,
                "price": price,
                "code": name,
                "available": True,
            })

    return types


# ── 結帳表單解析 ────────────────────────────────────────────────

def parse_checkout_form(html: str) -> dict:
    """解析結帳頁面，提取表單欄位和付款方式"""
    fields: dict[str, str] = {}

    # 提取隱藏欄位
    for match in re.finditer(r'<input[^>]+type=["\']hidden["\'][^>]*>', html):
        tag = match.group(0)
        name_m = re.search(r'name=["\']([^"\']+)', tag)
        value_m = re.search(r'value=["\']([^"\']*)', tag)
        if name_m:
            fields[name_m.group(1)] = value_m.group(1) if value_m else ""

    # 提取付款方式
    payment_methods: list[dict] = []
    for match in re.finditer(
        r'<(?:input|label)[^>]*(?:payment|pay)[^>]*>([^<]*)',
        html,
        re.IGNORECASE,
    ):
        text = _clean_text(match.group(1))
        if text:
            payment_methods.append({"name": text})

    # 提取總金額
    total = _search(r'(?:總計|Total|合計)[^<]*?(?:NT\$?|TWD)\s*([\d,]+)', html)

    return {
        "fields": fields,
        "payment_methods": payment_methods,
        "total": total,
    }


# ── 頁面狀態偵測 ────────────────────────────────────────────────

def detect_login_required(html: str, url: str = "") -> bool:
    """偵測是否需要登入"""
    if "Member/Login" in url or "Member/Deposit" in url:
        return True
    if re.search(r'(?:登入|Login|Sign\s*In)', html, re.IGNORECASE):
        if re.search(r'<form[^>]*(?:login|member|sign)', html, re.IGNORECASE):
            return True
        if re.search(r'<input[^>]*(?:password|passwd)', html, re.IGNORECASE):
            return True
    return False


def detect_error_state(html: str) -> dict | None:
    """偵測 SweetAlert2 或其他錯誤彈窗"""
    # SweetAlert2 彈窗
    swal_match = re.search(
        r'(?:swal2-(?:title|content|html-container))["\'][^>]*>([^<]+)',
        html,
        re.IGNORECASE,
    )
    if swal_match:
        message = _clean_text(swal_match.group(1))
        error_type = "error"
        if re.search(r'售完|sold\s*out|額滿', message, re.IGNORECASE):
            error_type = "sold_out"
        elif re.search(r'逾時|expired|timeout|過期', message, re.IGNORECASE):
            error_type = "session_expired"
        elif re.search(r'上限|limit|超過', message, re.IGNORECASE):
            error_type = "limit_reached"
        return {"type": error_type, "message": message}

    # JavaScript alert / confirm
    alert_match = re.search(r'(?:alert|Swal\.fire)\s*\(\s*["\']([^"\']+)', html)
    if alert_match:
        return {"type": "error", "message": _clean_text(alert_match.group(1))}

    return None


def detect_page_type(html: str, url: str = "") -> str:
    """
    根據 HTML 內容和 URL 判斷威秀頁面類型。
    回傳: "theater_selection" | "movie_list" | "seat_selection" |
          "ticket_type" | "checkout" | "login_required" | "error" | "unknown"
    """
    if detect_login_required(html, url):
        return "login_required"

    if detect_error_state(html):
        return "error"

    # 結帳/付款頁
    if re.search(r'(?:結帳|付款|checkout|payment|確認訂單)', html, re.IGNORECASE):
        if re.search(r'<form[^>]*(?:payment|checkout|order)', html, re.IGNORECASE):
            return "checkout"

    # 座位選擇
    if re.search(r'(?:seat|座位|選位)', html, re.IGNORECASE):
        if re.search(r'(?:seat-map|seatMap|seat_map|座位圖|選擇座位)', html, re.IGNORECASE):
            return "seat_selection"

    # 票種選擇
    if re.search(r'(?:票種|ticket.?type|選擇票種|全票|優待票)', html, re.IGNORECASE):
        if re.search(r'<select[^>]*(?:ticket|qty|quantity)', html, re.IGNORECASE):
            return "ticket_type"

    # 電影列表 / 場次
    if re.search(r'(?:場次|showtime|movie.?list|上映)', html, re.IGNORECASE):
        return "movie_list"

    # 影城選擇（預設售票首頁）
    if "vsTicketing" in url or "ticket.aspx" in url:
        return "theater_selection"

    return "unknown"
