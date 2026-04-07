"""威秀影城 HTML 解析器測試"""

from __future__ import annotations

import pytest

from ticket_bot.platforms.vieshow_parser import (
    THEATER_CODES,
    THEATER_NAME_TO_CODE,
    detect_error_state,
    detect_login_required,
    detect_page_type,
    matches_theater,
    parse_checkout_form,
    parse_movie_list,
    parse_seat_map,
    parse_theater_list,
    parse_ticket_types,
)


# ── 影城代碼對照 ───────────────────────────────────────────────

class TestTheaterCodes:
    def test_all_20_theaters(self):
        assert len(THEATER_CODES) == 20

    def test_known_codes(self):
        assert THEATER_CODES["TP"] == "信義威秀"
        assert THEATER_CODES["MU"] == "松仁MUVIE ONE"
        assert THEATER_CODES["KS"] == "高雄威秀"
        assert THEATER_CODES["HL"] == "花蓮威秀"

    def test_reverse_mapping(self):
        assert THEATER_NAME_TO_CODE["信義威秀"] == "TP"
        assert THEATER_NAME_TO_CODE["高雄威秀"] == "KS"


# ── matches_theater ────────────────────────────────────────────

class TestMatchesTheater:
    def test_match_by_code(self):
        assert matches_theater("1|TP 信義威秀", "TP") is True

    def test_match_by_code_lowercase(self):
        assert matches_theater("1|TP 信義威秀", "tp") is True

    def test_match_by_name_keyword(self):
        assert matches_theater("信義威秀 Xinyi", "信義") is True

    def test_no_match(self):
        assert matches_theater("信義威秀", "板橋") is False

    def test_empty_keyword(self):
        assert matches_theater("信義威秀", "") is False

    def test_code_resolves_to_name(self):
        # TP code should also match when the text contains the theater name
        assert matches_theater("信義威秀影城", "TP") is True


# ── parse_theater_list ─────────────────────────────────────────

class TestParseTheaterList:
    def test_parse_select_options(self):
        html = """
        <select name="cinema" id="cinemaSelector">
            <option value="">-- 請選擇 --</option>
            <option value="1|TP" selected>信義威秀</option>
            <option value="21|MU">松仁MUVIE ONE</option>
            <option value="3|NL">南港威秀</option>
        </select>
        """
        result = parse_theater_list(html)
        assert len(result) == 3
        assert result[0]["code"] == "1|TP"
        assert result[0]["name"] == "信義威秀"
        assert result[0]["selected"] is True
        assert result[1]["selected"] is False

    def test_fallback_to_static_list(self):
        html = "<div>No select element here</div>"
        result = parse_theater_list(html)
        assert len(result) == 20
        assert any(t["code"] == "TP" for t in result)


# ── parse_movie_list ───────────────────────────────────────────

class TestParseMovieList:
    def test_parse_movie_with_class(self):
        html = """
        <div class="movieItem" data-id="123">
            <h3 class="title">復仇者聯盟：乙太噩夢</h3>
            <span class="showtime">19:30</span>
            <span class="showtime">21:00</span>
        </div>
        """
        result = parse_movie_list(html)
        assert len(result) >= 1
        assert "復仇者聯盟" in result[0]["title"]

    def test_fallback_regex_extraction(self):
        html = """
        <div>
            <h2>蜘蛛人：乖乖返鄉</h2>
            <h2>星際大戰：安多</h2>
        </div>
        """
        result = parse_movie_list(html)
        assert len(result) >= 2

    def test_empty_html(self):
        result = parse_movie_list("")
        assert result == []


# ── parse_seat_map ─────────────────────────────────────────────

class TestParseSeatMap:
    def test_parse_data_attributes(self):
        html = """
        <div class="seat-map">
            <div class="seat" data-row="F" data-col="12"></div>
            <div class="seat" data-row="F" data-col="13"></div>
            <div class="seat occupied" data-row="F" data-col="14"></div>
        </div>
        """
        result = parse_seat_map(html)
        assert result["total"] == 3
        assert len(result["available"]) == 2
        assert len(result["occupied"]) == 1
        assert result["available"][0]["row"] == "F"
        assert result["available"][0]["number"] == 12

    def test_parse_id_pattern(self):
        html = """
        <td id="seat_A_1" class="seat"></td>
        <td id="seat_A_2" class="seat sold"></td>
        """
        result = parse_seat_map(html)
        assert result["total"] == 2
        assert len(result["available"]) == 1
        assert len(result["occupied"]) == 1

    def test_no_seats(self):
        result = parse_seat_map("<div>Loading...</div>")
        assert result["total"] == 0
        assert result["available"] == []


# ── parse_ticket_types ─────────────────────────────────────────

class TestParseTicketTypes:
    def test_parse_select_options(self):
        html = """
        <select name="ticketType">
            <option value="full">全票 $350</option>
            <option value="student">優待票 $280</option>
            <option value="ishow">iShow會員票 $300</option>
        </select>
        """
        result = parse_ticket_types(html)
        assert len(result) == 3
        assert result[0]["name"] == "全票 $350"
        assert result[0]["price"] == 350
        assert result[0]["code"] == "full"

    def test_parse_text_patterns(self):
        html = """
        <div>全票 NT$350</div>
        <div>優待票 NT$280</div>
        """
        result = parse_ticket_types(html)
        assert len(result) >= 2

    def test_empty_html(self):
        result = parse_ticket_types("")
        assert result == []


# ── parse_checkout_form ────────────────────────────────────────

class TestParseCheckoutForm:
    def test_parse_hidden_fields(self):
        html = """
        <form>
            <input type="hidden" name="__VIEWSTATE" value="abc123">
            <input type="hidden" name="_csrf" value="token456">
        </form>
        """
        result = parse_checkout_form(html)
        assert result["fields"]["__VIEWSTATE"] == "abc123"
        assert result["fields"]["_csrf"] == "token456"

    def test_parse_total(self):
        html = '<div>總計: NT$700</div>'
        result = parse_checkout_form(html)
        assert result["total"] == "700"

    def test_no_total(self):
        result = parse_checkout_form("<div>empty</div>")
        assert result["total"] == ""


# ── detect_login_required ──────────────────────────────────────

class TestDetectLoginRequired:
    def test_url_contains_login(self):
        assert detect_login_required("", url="https://sales.vscinemas.com.tw/Member/Login") is True

    def test_url_contains_deposit(self):
        assert detect_login_required("", url="https://sales.vscinemas.com.tw/Member/Deposit") is True

    def test_form_with_password(self):
        html = """
        <form action="/Member/Login" method="post">
            <input type="email" name="email">
            <input type="password" name="password">
            <button type="submit">登入</button>
        </form>
        """
        assert detect_login_required(html) is True

    def test_normal_page(self):
        html = "<div>Welcome to VIESHOW</div>"
        assert detect_login_required(html) is False


# ── detect_error_state ─────────────────────────────────────────

class TestDetectErrorState:
    def test_swal2_popup(self):
        html = '<div class="swal2-title">售完</div>'
        result = detect_error_state(html)
        assert result is not None
        assert result["type"] == "sold_out"
        assert "售完" in result["message"]

    def test_session_expired(self):
        html = '<div class="swal2-content">您的 session 已逾時，請重新操作</div>'
        result = detect_error_state(html)
        assert result is not None
        assert result["type"] == "session_expired"

    def test_limit_reached(self):
        html = '<div class="swal2-html-container">已超過每日購買上限</div>'
        result = detect_error_state(html)
        assert result is not None
        assert result["type"] == "limit_reached"

    def test_js_alert(self):
        html = "Swal.fire('系統繁忙，請稍後再試')"
        result = detect_error_state(html)
        assert result is not None
        assert result["type"] == "error"

    def test_no_error(self):
        html = "<div>Normal page content</div>"
        assert detect_error_state(html) is None


# ── detect_page_type ───────────────────────────────────────────

class TestDetectPageType:
    def test_login_page(self):
        html = '<form action="/Member/Login"><input type="password"></form>'
        assert detect_page_type(html, url="https://sales.vscinemas.com.tw/Member/Login") == "login_required"

    def test_error_page(self):
        html = '<div class="swal2-title">Error</div>'
        assert detect_page_type(html) == "error"

    def test_theater_selection(self):
        html = "<div>Choose theater</div>"
        assert detect_page_type(html, url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx") == "theater_selection"

    def test_seat_selection(self):
        html = '<div class="seat-map"><div class="seat"></div></div><div>選擇座位</div>'
        assert detect_page_type(html) == "seat_selection"

    def test_ticket_type(self):
        html = '<div>選擇票種</div><select name="ticketType"><option>全票</option></select>'
        assert detect_page_type(html) == "ticket_type"

    def test_checkout(self):
        html = '<div>結帳</div><form action="/payment/checkout"><input></form>'
        assert detect_page_type(html) == "checkout"

    def test_movie_list(self):
        html = '<div>場次</div><a href="#">19:30</a><a href="#">21:00</a>' + '<button>x</button>' * 10
        assert detect_page_type(html) == "movie_list"

    def test_unknown(self):
        html = "<div>Something else</div>"
        assert detect_page_type(html) == "unknown"
