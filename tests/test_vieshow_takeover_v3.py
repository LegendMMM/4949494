from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ticket_bot.config import AppConfig, EventConfig, SessionConfig
import ticket_bot.vieshow_takeover_v3.flow as flow_module
from ticket_bot.vieshow_takeover_v3.flow import VieShowTakeoverV3, read_showtime_options


def _make_bot() -> VieShowTakeoverV3:
    cfg = AppConfig()
    cfg.vieshow.takeover.enabled = True
    cfg.vieshow.takeover.cdp_url = "http://127.0.0.1:9222"
    cfg.vieshow.takeover.page_url_substring = "vscinemas.com.tw"
    event = EventConfig(
        name="VieShow",
        platform="vieshow",
        url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
        ticket_count=2,
    )
    return VieShowTakeoverV3(cfg, event, session=SessionConfig())


@pytest.mark.asyncio
async def test_read_showtime_options_connects_and_returns(monkeypatch):
    calls: list[str] = []

    class FakeCDP:
        async def connect(self, cdp_url: str, page_url_substring: str):
            calls.append(f"connect:{cdp_url}:{page_url_substring}")
            return None

        async def evaluate(self, _expr: str):
            calls.append("evaluate")
            return [{"optionId": "0", "text": "16:40"}]

        async def close(self):
            calls.append("close")
            return None

    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.flow.CDPTakeoverEngine", FakeCDP)
    result = await read_showtime_options(cdp_url="http://127.0.0.1:9222", page_url_substring="ticket.aspx")

    assert result == [{"optionId": "0", "text": "16:40"}]
    assert calls == [
        "connect:http://127.0.0.1:9222:ticket.aspx",
        "evaluate",
        "close",
    ]


def test_state_from_url_prioritizes_ticket_page():
    bot = _make_bot()
    state = bot._state_from_url(
        "https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx?cinema=35|NL&movie=HO00017160",
        "seat_selection",
    )
    assert state == "ticket_showtime"


def test_state_from_url_maps_booking_unknown_to_booking_option():
    bot = _make_bot()
    state = bot._state_from_url(
        "https://www.vscinemas.com.tw/vsTicketing/ticketing/booking.aspx?cinemacode=2&txtSessionId=1152526",
        "booking_unknown",
    )
    assert state == "booking_option"


@pytest.mark.asyncio
async def test_booking_option_branch_forces_rules_flow_when_checkbox_present(monkeypatch):
    bot = _make_bot()
    states = iter(
        [
            {
                "state": "booking_option",
                "url": "https://www.vscinemas.com.tw/vsTicketing/ticketing/booking.aspx?cinemacode=2&txtSessionId=1",
            },
            {
                "state": "order_confirm",
                "url": "https://sales.vscinemas.com.tw/LiveTicketT2/Home/OrderConfirm",
            },
        ]
    )
    events: list[str] = []

    async def fake_wait_page_ready(timeout: float = 1.8):
        events.append(f"ready:{timeout}")
        return None

    async def fake_read_state():
        return next(states)

    async def fake_has_rules_checkbox():
        events.append("has_rules")
        return True

    async def fake_click_rules_agree():
        events.append("rules_agree")
        return True

    async def fake_click_rules_continue():
        events.append("rules_continue")
        return True

    async def fake_close_dialog():
        return False

    async def fake_sleep(_value: float):
        return None

    monkeypatch.setattr(bot, "_wait_page_ready", fake_wait_page_ready)
    monkeypatch.setattr(bot, "_read_state", fake_read_state)
    monkeypatch.setattr(bot, "_has_rules_checkbox", fake_has_rules_checkbox)
    monkeypatch.setattr(bot, "_click_rules_agree", fake_click_rules_agree)
    monkeypatch.setattr(bot, "_click_rules_continue", fake_click_rules_continue)
    monkeypatch.setattr(bot, "_close_dialog_if_present", fake_close_dialog)
    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.flow.asyncio.sleep", fake_sleep)

    result = await bot._run_state_machine()
    assert result is True
    assert "has_rules" in events
    assert "rules_agree" in events
    assert "rules_continue" in events


@pytest.mark.asyncio
async def test_wait_for_sale_window_waits_before_target(monkeypatch):
    bot = _make_bot()
    future = datetime.now() + timedelta(seconds=10)
    bot._sale_time_dt = future  # noqa: SLF001
    sleeps: list[float] = []

    async def fake_sleep(value: float):
        sleeps.append(value)
        return None

    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.flow.asyncio.sleep", fake_sleep)
    ready = await bot._wait_for_sale_window()

    assert ready is False
    assert sleeps


@pytest.mark.asyncio
async def test_select_general_full_ticket_count_prefers_full_in_general(monkeypatch):
    bot = _make_bot()
    bot.event.ticket_count = 2
    calls: list[str] = []

    async def fake_click_button(include_keywords, **_kwargs):
        calls.append(f"click:{include_keywords[0]}")
        return True

    async def fake_evaluate(expr: str):
        if "document.querySelectorAll(\"select\")" in expr:
            return [
                {
                    "domIndex": 0,
                    "rowText": "優待票",
                    "label": "一般票種 優待票",
                    "visible": True,
                    "selectedIndex": 0,
                    "options": [
                        {"index": 0, "text": "0", "value": "0"},
                        {"index": 1, "text": "1", "value": "1"},
                    ],
                },
                {
                    "domIndex": 1,
                    "rowText": "全票",
                    "label": "一般票種 全票",
                    "visible": True,
                    "selectedIndex": 0,
                    "options": [
                        {"index": 0, "text": "0", "value": "0"},
                        {"index": 1, "text": "1", "value": "1"},
                        {"index": 2, "text": "2", "value": "2"},
                    ],
                },
            ]
        return False

    async def fake_set_select_index(dom_index: int, target_index: int):
        calls.append(f"set:{dom_index}:{target_index}")
        return True

    monkeypatch.setattr(bot, "_click_button", fake_click_button)
    monkeypatch.setattr(bot.cdp, "evaluate", fake_evaluate)
    monkeypatch.setattr(bot, "_set_select_index", fake_set_select_index)

    result = await bot._select_general_full_ticket_count()

    assert result is True
    assert "set:1:2" in calls


@pytest.mark.asyncio
async def test_run_state_machine_ticket_to_order_confirm(monkeypatch):
    bot = _make_bot()
    states = iter(
        [
            {"state": "ticket_showtime", "url": "https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx"},
            {"state": "booking_option", "url": "https://www.vscinemas.com.tw/vsTicketing/ticketing/booking.aspx"},
            {"state": "booking_rules", "url": "https://www.vscinemas.com.tw/vsTicketing/ticketing/booking.aspx"},
            {"state": "ticket_type", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/?agree=on"},
            {"state": "seat_selection", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/Home/SelectSeats"},
            {"state": "order_confirm", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/Home/OrderConfirm"},
        ]
    )
    events: list[str] = []

    async def fake_wait_page_ready(timeout: float = 1.8):
        events.append(f"ready:{timeout}")
        return None

    async def fake_read_state():
        return next(states)

    async def fake_wait_for_sale_window():
        events.append("sale_window")
        return True

    async def fake_click_showtime():
        events.append("showtime")
        return True

    async def fake_click_booking_option():
        events.append("booking_option")
        return True

    async def fake_has_rules_checkbox():
        return False

    async def fake_click_rules_agree():
        events.append("rules_agree")
        return True

    async def fake_click_rules_continue():
        events.append("rules_continue")
        return True

    async def fake_select_full_ticket_count():
        events.append("ticket_count")
        return True

    async def fake_click_primary_continue():
        events.append("primary_continue")
        return True

    async def fake_close_dialog():
        return False

    async def fake_sleep(_value: float):
        return None

    monkeypatch.setattr(bot, "_wait_page_ready", fake_wait_page_ready)
    monkeypatch.setattr(bot, "_read_state", fake_read_state)
    monkeypatch.setattr(bot, "_wait_for_sale_window", fake_wait_for_sale_window)
    monkeypatch.setattr(bot, "_click_showtime", fake_click_showtime)
    monkeypatch.setattr(bot, "_click_booking_option_top", fake_click_booking_option)
    monkeypatch.setattr(bot, "_has_rules_checkbox", fake_has_rules_checkbox)
    monkeypatch.setattr(bot, "_click_rules_agree", fake_click_rules_agree)
    monkeypatch.setattr(bot, "_click_rules_continue", fake_click_rules_continue)
    monkeypatch.setattr(bot, "_select_general_full_ticket_count", fake_select_full_ticket_count)
    monkeypatch.setattr(bot, "_click_primary_continue", fake_click_primary_continue)
    monkeypatch.setattr(bot, "_close_dialog_if_present", fake_close_dialog)
    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.flow.asyncio.sleep", fake_sleep)

    result = await bot._run_state_machine()

    assert result is True
    assert "showtime" in events
    assert "booking_option" in events
    assert "rules_agree" in events
    assert "rules_continue" in events
    assert "ticket_count" in events
    assert "primary_continue" in events


@pytest.mark.asyncio
async def test_click_primary_continue_uses_js_fallback(monkeypatch):
    bot = _make_bot()

    async def fake_evaluate(expr: str):
        if expr == flow_module.READ_PRIMARY_CONTINUE_BUTTON_JS:
            return None
        if expr == flow_module.CLICK_PRIMARY_CONTINUE_JS:
            return True
        return None

    monkeypatch.setattr(bot.cdp, "evaluate", fake_evaluate)

    result = await bot._click_primary_continue()
    assert result is True


@pytest.mark.asyncio
async def test_ticket_type_skips_reselect_when_ready(monkeypatch):
    bot = _make_bot()
    states = iter(
        [
            {"state": "ticket_type", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/?agree=on"},
            {"state": "ticket_type", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/?agree=on"},
            {"state": "order_confirm", "url": "https://sales.vscinemas.com.tw/LiveTicketT2/Home/OrderConfirm"},
        ]
    )
    events: list[str] = []

    async def fake_wait_page_ready(timeout: float = 1.5):
        events.append(f"ready:{timeout}")
        return None

    async def fake_read_state():
        return next(states)

    async def fake_select_full_ticket_count():
        events.append("ticket_count")
        bot._ticket_qty_ready = True  # noqa: SLF001
        return True

    async def fake_click_primary_continue():
        events.append("primary_continue")
        return True

    async def fake_close_dialog():
        return False

    async def fake_sleep(_value: float):
        return None

    monkeypatch.setattr(bot, "_wait_page_ready", fake_wait_page_ready)
    monkeypatch.setattr(bot, "_read_state", fake_read_state)
    monkeypatch.setattr(bot, "_select_general_full_ticket_count", fake_select_full_ticket_count)
    monkeypatch.setattr(bot, "_click_primary_continue", fake_click_primary_continue)
    monkeypatch.setattr(bot, "_close_dialog_if_present", fake_close_dialog)
    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.flow.asyncio.sleep", fake_sleep)

    result = await bot._run_state_machine()

    assert result is True
    assert events.count("ticket_count") == 1
