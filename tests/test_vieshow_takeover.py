from __future__ import annotations

from datetime import timedelta
import pytest

from ticket_bot.config import AppConfig, EventConfig, TakeoverConfig, VieShowConfig
from ticket_bot.platforms.vieshow import DETECT_STATE_JS, READ_TAKEOVER_FLOW_HINTS_JS, VieShowBot


class _DummyEngine:
    async def launch(self, **kwargs):
        return None

    async def new_page(self, url: str = ""):
        raise AssertionError("legacy browser page should not be created in these tests")

    async def close(self):
        return None


class _ConstantCDP:
    def __init__(self, value):
        self.value = value
        self.clicks = []
        self.key_events = []
        self.mouse_moves = []
        self.wheel_events = []

    async def evaluate(self, expression):
        return self.value

    async def human_mouse_move(self, from_xy, to_xy, duration_ms=300, steps=15, rng=None):
        self.mouse_moves.append((from_xy, to_xy, duration_ms, steps))
        return [from_xy, to_xy]

    async def dispatch_click(self, x, y):
        self.clicks.append((x, y))

    async def dispatch_mouse_wheel(self, x, y, delta_x=0, delta_y=0):
        self.wheel_events.append((x, y, delta_x, delta_y))

    async def dispatch_key_event(self, *args, **kwargs):
        self.key_events.append((args, kwargs))
        return {}


def _make_bot(monkeypatch, *, takeover: bool) -> VieShowBot:
    monkeypatch.setattr("ticket_bot.platforms.vieshow.create_engine", lambda _name: _DummyEngine())
    cfg = AppConfig(
        vieshow=VieShowConfig(
            seat_preference="center",
            takeover=TakeoverConfig(enabled=takeover, cdp_url="http://127.0.0.1:9222"),
            takeover_mode=takeover,
            attach_cdp_url="http://127.0.0.1:9222" if takeover else "",
        )
    )
    event = EventConfig(
        name="VieShow",
        platform="vieshow",
        url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
        ticket_count=2,
    )
    return VieShowBot(cfg, event)


@pytest.mark.asyncio
async def test_run_dispatches_takeover(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)

    async def fake_run_takeover():
        return True

    async def fake_run_legacy():
        raise AssertionError("legacy path should not run")

    monkeypatch.setattr(bot, "run_takeover", fake_run_takeover)
    monkeypatch.setattr(bot, "run_legacy", fake_run_legacy)

    assert await bot.run() is True


@pytest.mark.asyncio
async def test_run_dispatches_legacy(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=False)

    async def fake_run_takeover():
        raise AssertionError("takeover path should not run")

    async def fake_run_legacy():
        return True

    monkeypatch.setattr(bot, "run_takeover", fake_run_takeover)
    monkeypatch.setattr(bot, "run_legacy", fake_run_legacy)

    assert await bot.run() is True


def test_pick_best_seats_prefers_center_group(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    seats = [
        {"id": "A1", "row": "A", "x": 100, "y": 100, "width": 10, "height": 10},
        {"id": "A2", "row": "A", "x": 120, "y": 100, "width": 10, "height": 10},
        {"id": "A3", "row": "A", "x": 140, "y": 100, "width": 10, "height": 10},
        {"id": "A4", "row": "A", "x": 160, "y": 100, "width": 10, "height": 10},
        {"id": "A5", "row": "A", "x": 180, "y": 100, "width": 10, "height": 10},
    ]

    picked = bot._pick_best_seats(seats, count=2, preference="center")

    assert [seat["id"] for seat in picked] == ["A2", "A3"]


def test_filter_available_seats_excludes_disabled_or_occupied(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    seats = [
        {"id": "A1", "className": "seat available", "text": "", "disabled": False, "width": 10, "height": 10},
        {"id": "A2", "className": "seat occupied", "text": "", "disabled": False, "width": 10, "height": 10},
        {"id": "A3", "className": "seat", "text": "", "disabled": True, "width": 10, "height": 10},
    ]

    filtered = bot._filter_available_seats(seats)

    assert [seat["id"] for seat in filtered] == ["A1"]


@pytest.mark.asyncio
async def test_takeover_happy_path_state_flow(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    states = iter(["booking_landing", "ticket_type", "seat_selection", "checkout"])
    events = []
    transitions = {
        "booking_landing": "ticket_type",
        "ticket_type": "seat_selection",
        "seat_selection": "checkout",
    }

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete"}

    async def fake_enter_booking():
        events.append("booking_landing")
        return True

    async def fake_select_ticket_type():
        events.append("ticket_type")
        return True

    async def fake_grab_seats():
        events.append("seat_selection")
        return ["G13", "G14"]

    async def fake_checkout():
        events.append("checkout")
        bot.last_success_info = "checkout reached"

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return transitions[previous_state]

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_enter_booking_flow_takeover", fake_enter_booking)
    monkeypatch.setattr(bot, "_select_ticket_type_takeover", fake_select_ticket_type)
    monkeypatch.setattr(bot, "_grab_seats_takeover", fake_grab_seats)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert events == [
        "booking_landing",
        "wait:booking_landing",
        "ticket_type",
        "wait:ticket_type",
        "seat_selection",
        "wait:seat_selection",
        "checkout",
    ]


@pytest.mark.asyncio
async def test_takeover_quick_booking_state_flow(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    states = iter(["quick_booking", "ticket_type", "checkout"])
    events = []

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete"}

    async def fake_rush_quick_booking():
        events.append("quick_booking")
        return True

    async def fake_select_ticket_type():
        events.append("ticket_type")
        return True

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return "checkout"

    async def fake_checkout():
        events.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_rush_quick_booking_takeover", fake_rush_quick_booking)
    monkeypatch.setattr(bot, "_select_ticket_type_takeover", fake_select_ticket_type)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert events == ["quick_booking", "ticket_type", "wait:ticket_type", "checkout"]


@pytest.mark.asyncio
async def test_takeover_busy_loading_waits_before_continuing(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    states = iter(["seat_selection", "seat_selection", "checkout"])
    activity = iter(
        [
            {"busy": True, "readyState": "loading"},
            {"busy": False, "readyState": "complete"},
            {"busy": False, "readyState": "complete"},
        ]
    )
    events = []

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return next(activity)

    async def fake_wait_for_ready(timeout=5.0, poll=0.06):
        events.append("wait_for_ready")
        return True

    async def fake_grab_seats():
        events.append("grab_seats")
        return ["G13", "G14"]

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return "checkout"

    async def fake_checkout():
        events.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_wait_for_page_ready_takeover", fake_wait_for_ready)
    monkeypatch.setattr(bot, "_grab_seats_takeover", fake_grab_seats)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert events[0] == "wait_for_ready"
    assert events.index("wait_for_ready") < events.index("grab_seats")
    assert "checkout" in events


@pytest.mark.asyncio
async def test_takeover_quick_booking_waits_before_sale_time(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    calls = []

    base_now = bot._now_local()

    monkeypatch.setattr(bot, "_parse_sale_time_takeover", lambda: base_now + timedelta(seconds=30))
    monkeypatch.setattr(bot, "_now_local", lambda: base_now)

    async def fake_click_best_button(_keywords):
        calls.append("click")
        return True

    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)

    result = await bot._rush_quick_booking_takeover()

    assert result is False
    assert calls == []


@pytest.mark.asyncio
async def test_takeover_quick_booking_burst_enters_ticket_flow(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    calls = []

    monkeypatch.setattr(bot, "_parse_sale_time_takeover", lambda: None)

    async def fake_detect_state():
        return "ticket_type"

    async def fake_click_best_button(keywords):
        calls.append(tuple(keywords))
        return True

    async def fake_handle_error():
        raise AssertionError("error handler should not be called in this path")

    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_handle_error_takeover", fake_handle_error)

    result = await bot._rush_quick_booking_takeover()

    assert result is True
    assert calls
    assert "前往訂票" in calls[0]


@pytest.mark.asyncio
async def test_takeover_error_and_login_required_paths(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({"message": "售完", "button": {"x": 10, "y": 20}})

    states = iter(["error", "login_required", "checkout"])
    reports = []

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({"message": "售完", "button": {"x": 10, "y": 20}})

    async def fake_detect_state():
        return next(states)

    async def fake_checkout():
        reports.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)
    monkeypatch.setattr(bot, "_report", lambda msg: reports.append(msg))

    assert await bot.run_takeover() is True
    assert any("售完" in msg for msg in reports)
    assert any("請先在 Chrome 中完成登入" in msg for msg in reports)
    assert "checkout" in reports


@pytest.mark.asyncio
async def test_takeover_no_seats_returns_empty(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP([])

    async def fake_click_best_button(_keywords):
        return False

    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)

    picked = await bot._grab_seats_takeover()

    assert picked == []
    assert bot.cdp.clicks == []


@pytest.mark.asyncio
async def test_takeover_no_matching_ticket_controls(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({"selects": [], "checkboxes": [], "buttons": []})

    result = await bot._select_ticket_type_takeover()

    assert result is False
    assert bot.cdp.clicks == []


@pytest.mark.asyncio
async def test_takeover_ticket_type_does_not_continue_when_selection_not_applied(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    async def fake_expand(_section_label):
        return {
            "selects": [
                {
                    "name": "full-ticket",
                    "label": "0 1 2 3 4",
                    "rowText": "全票 $300 0 1 2 3 4",
                    "headers": ["一般票種"],
                    "visible": True,
                    "x": 560,
                    "y": 1180,
                    "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                    "selectedIndex": 0,
                }
            ],
            "checkboxes": [],
            "buttons": [{"label": "繼續", "x": 1120, "y": 1710}],
            "viewportHeight": 900,
        }

    async def fake_read_controls():
        return await fake_expand("一般票種")

    async def fake_set_select_value(_control, _desired_count):
        return False

    async def fake_wait_ready(*_args, **_kwargs):
        return False

    async def fail_click(_keywords):
        raise AssertionError("continue must not be clicked when ticket selection fails")

    monkeypatch.setattr(bot, "_expand_ticket_section_takeover", fake_expand)
    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)
    monkeypatch.setattr(bot, "_set_select_value_takeover", fake_set_select_value)
    monkeypatch.setattr(bot, "_wait_for_page_ready_takeover", fake_wait_ready)
    monkeypatch.setattr(bot, "_click_best_button_takeover", fail_click)

    result = await bot._select_ticket_type_takeover()

    assert result is False


@pytest.mark.asyncio
async def test_takeover_ticket_type_expands_general_section_then_selects_full_ticket(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    calls = []
    selected = {}
    snapshots = iter(
        [
            {
                "selects": [
                    {"name": "bundle", "label": "超級套票 $1059", "headers": ["優惠套票"], "visible": True, "x": 500, "y": 600, "options": [{"index": 0, "text": "0", "value": "0"}], "selectedIndex": 0},
                ],
                "checkboxes": [],
                "buttons": [{"label": "一般票種", "x": 420, "y": 1030}],
                "viewportHeight": 900,
            },
            {
                "selects": [
                    {
                        "name": "full-ticket",
                        "label": "0 1 2 3 4",
                        "rowText": "全票 $300 0 1 2 3 4",
                        "headers": ["一般票種"],
                        "visible": True,
                        "x": 560,
                        "y": 1180,
                        "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                        "selectedIndex": 0,
                    }
                ],
                "checkboxes": [],
                "buttons": [
                    {"label": "一般票種", "x": 420, "y": 1030},
                    {"label": "繼續", "x": 1120, "y": 1710},
                ],
                "viewportHeight": 900,
            },
        ]
    )

    async def fake_read_controls():
        try:
            return next(snapshots)
        except StopIteration:
            return {
                "selects": [
                    {
                        "name": "full-ticket",
                        "label": "0 1 2 3 4",
                        "rowText": "全票 $300 0 1 2 3 4",
                        "headers": ["一般票種"],
                        "visible": True,
                        "x": 560,
                        "y": 1180,
                        "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                        "selectedIndex": 0,
                    }
                ],
                "checkboxes": [],
                "buttons": [
                    {"label": "一般票種", "x": 420, "y": 1030},
                    {"label": "繼續", "x": 1120, "y": 1710},
                ],
                "viewportHeight": 900,
            }

    async def fake_click_best_button(keywords):
        calls.append(tuple(keywords))
        return True

    async def fake_set_select_value(control, desired_count):
        selected["control"] = control
        selected["count"] = desired_count
        return True

    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)
    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)
    monkeypatch.setattr(bot, "_set_select_value_takeover", fake_set_select_value)

    result = await bot._select_ticket_type_takeover()

    assert result is True
    assert calls[0] == ("一般票種",)
    assert "繼續" in calls[1]
    assert selected["control"]["name"] == "full-ticket"
    assert selected["count"] == 2


@pytest.mark.asyncio
async def test_takeover_ticket_type_prefers_full_ticket_over_discount_ticket(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    selected = {}

    async def fake_read_controls(_section_label):
        return {
            "selects": [
                {
                    "name": "discount-ticket",
                    "label": "0 1 2 3 4",
                    "rowText": "優待票 $300 0 1 2 3 4",
                    "headers": ["一般票種"],
                    "visible": True,
                    "x": 560,
                    "y": 1240,
                    "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                    "selectedIndex": 0,
                },
                {
                    "name": "full-ticket",
                    "label": "0 1 2 3 4",
                    "rowText": "全票 $300 0 1 2 3 4",
                    "headers": ["一般票種"],
                    "visible": True,
                    "x": 560,
                    "y": 1180,
                    "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                    "selectedIndex": 0,
                },
            ],
            "checkboxes": [],
            "buttons": [{"label": "繼續", "x": 1120, "y": 1710}],
            "viewportHeight": 900,
        }

    async def fake_set_select_value(control, desired_count):
        selected["name"] = control["name"]
        selected["count"] = desired_count
        return True

    async def fake_click_best_button(_keywords):
        return True

    monkeypatch.setattr(bot, "_expand_ticket_section_takeover", fake_read_controls)
    monkeypatch.setattr(bot, "_set_select_value_takeover", fake_set_select_value)
    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)

    result = await bot._select_ticket_type_takeover()

    assert result is True
    assert selected["name"] == "full-ticket"
    assert selected["count"] == 2


@pytest.mark.asyncio
async def test_takeover_ticket_type_full_not_excluded_by_bundle_header(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    selected = {}

    async def fake_read_controls(_section_label):
        return {
            "selects": [
                {
                    "name": "full-ticket",
                    "label": "0 1 2 3 4",
                    "rowText": "全票 $350 0 1 2 3 4 0",
                    "headers": ["一般票種", "優惠套票", "0+"],
                    "visible": True,
                    "x": 560,
                    "y": 1180,
                    "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                    "selectedIndex": 0,
                },
                {
                    "name": "discount-ticket",
                    "label": "0 1 2 3 4",
                    "rowText": "優待票 $330 0 1 2 3 4 0",
                    "headers": ["一般票種", "優惠套票", "0+"],
                    "visible": True,
                    "x": 560,
                    "y": 1240,
                    "options": [{"index": i, "text": str(i), "value": str(i)} for i in range(5)],
                    "selectedIndex": 0,
                },
            ],
            "checkboxes": [],
            "buttons": [{"label": "繼續", "x": 1120, "y": 1710}],
            "viewportHeight": 900,
        }

    async def fake_set_select_value(control, desired_count):
        selected["name"] = control["name"]
        selected["count"] = desired_count
        return True

    async def fake_click_best_button(_keywords):
        return True

    monkeypatch.setattr(bot, "_expand_ticket_section_takeover", fake_read_controls)
    monkeypatch.setattr(bot, "_set_select_value_takeover", fake_set_select_value)
    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)

    result = await bot._select_ticket_type_takeover()

    assert result is True
    assert selected["name"] == "full-ticket"
    assert selected["count"] == 2


@pytest.mark.asyncio
async def test_takeover_seat_selection_uses_auto_assigned_seats_first(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP([])
    calls = []

    async def fake_click_best_button(keywords):
        calls.append(tuple(keywords))
        return True

    async def fake_detect_state():
        return "checkout"

    monkeypatch.setattr(bot, "_click_best_button_takeover", fake_click_best_button)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)

    picked = await bot._grab_seats_takeover()

    assert picked == ["auto-assigned"]
    assert calls[0] == ("繼續", "下一步", "continue", "next")


@pytest.mark.asyncio
async def test_takeover_click_best_button_scrolls_before_click(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    snapshots = iter(
        [
            {
                "selects": [],
                "checkboxes": [],
                "buttons": [{"label": "繼續", "x": 1200, "y": 1400}],
                "viewportHeight": 900,
            },
            {
                "selects": [],
                "checkboxes": [],
                "buttons": [{"label": "繼續", "x": 1200, "y": 420}],
                "viewportHeight": 900,
            },
        ]
    )

    async def fake_read_controls():
        try:
            return next(snapshots)
        except StopIteration:
            return {
                "selects": [],
                "checkboxes": [],
                "buttons": [{"label": "繼續", "x": 1200, "y": 420}],
                "viewportHeight": 900,
            }

    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)

    result = await bot._click_best_button_takeover(["繼續"])

    assert result is True
    assert bot.cdp.wheel_events
    assert bot.cdp.clicks == [(1200.0, 420.0)]


@pytest.mark.asyncio
async def test_takeover_click_best_button_prefers_exact_bottom_action(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    async def fake_read_controls():
        return {
            "selects": [],
            "checkboxes": [],
            "buttons": [
                {"label": "訂票記錄", "x": 830, "y": 38},
                {"label": "前往訂票", "x": 650, "y": 940},
            ],
            "viewportHeight": 1100,
        }

    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)

    result = await bot._click_best_button_takeover(["前往訂票", "訂票"])

    assert result is True
    assert bot.cdp.clicks == [(650.0, 940.0)]


@pytest.mark.asyncio
async def test_takeover_booking_landing_checks_agreement_and_clicks_continue(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP(
        {
            "selects": [],
            "checkboxes": [{"label": "我已閱讀並同意上述規定", "checked": False, "x": 100, "y": 200}],
            "buttons": [
                {"label": "訂票記錄", "x": 830, "y": 38},
                {"label": "前往訂票", "x": 300, "y": 400},
            ],
            "viewportHeight": 900,
        }
    )

    result = await bot._enter_booking_flow_takeover()

    assert result is True
    assert len(bot.cdp.clicks) == 2


@pytest.mark.asyncio
async def test_takeover_booking_landing_without_continue_button_returns_false(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP(
        {
            "selects": [],
            "checkboxes": [{"label": "我已閱讀並同意上述規定", "checked": False, "x": 100, "y": 200}],
            "buttons": [],
            "viewportHeight": 900,
        }
    )

    result = await bot._enter_booking_flow_takeover()

    assert result is False
    assert len(bot.cdp.clicks) == 1


@pytest.mark.asyncio
async def test_detect_takeover_state_reclassifies_ticket_type_to_pre_sale_wait(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)

    class _HintCDP:
        async def evaluate(self, expression):
            if expression == DETECT_STATE_JS:
                return "ticket_type"
            if expression == READ_TAKEOVER_FLOW_HINTS_JS:
                return {
                    "liveTicketLike": True,
                    "selectCount": 0,
                    "ticketSelectCount": 0,
                    "hasBookButton": False,
                    "hasPresaleMarker": True,
                }
            return None

    bot.cdp = _HintCDP()
    state = await bot._detect_takeover_state()
    assert state == "pre_sale_wait"


@pytest.mark.asyncio
async def test_detect_takeover_state_reclassifies_ticket_type_to_quick_booking(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)

    class _HintCDP:
        async def evaluate(self, expression):
            if expression == DETECT_STATE_JS:
                return "ticket_type"
            if expression == READ_TAKEOVER_FLOW_HINTS_JS:
                return {
                    "liveTicketLike": True,
                    "selectCount": 4,
                    "ticketSelectCount": 0,
                    "hasBookButton": True,
                    "hasPresaleMarker": False,
                }
            return None

    bot.cdp = _HintCDP()
    state = await bot._detect_takeover_state()
    assert state == "quick_booking"


@pytest.mark.asyncio
async def test_takeover_pre_sale_wait_state_flow(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    states = iter(["pre_sale_wait", "ticket_type", "checkout"])
    events = []

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete"}

    async def fake_rush():
        events.append("pre_sale_wait")
        return True

    async def fake_select_ticket_type():
        events.append("ticket_type")
        return True

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return "checkout"

    async def fake_checkout():
        events.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_seconds_until_sale_takeover", lambda: 0.0)
    monkeypatch.setattr(bot, "_rush_quick_booking_takeover", fake_rush)
    monkeypatch.setattr(bot, "_select_ticket_type_takeover", fake_select_ticket_type)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert events == ["pre_sale_wait", "ticket_type", "wait:ticket_type", "checkout"]


@pytest.mark.asyncio
async def test_takeover_pre_sale_wait_without_sale_time_does_not_click(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    reports = []
    detect_calls = {"count": 0}

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        detect_calls["count"] += 1
        if detect_calls["count"] >= 2:
            bot.request_stop()
        return "pre_sale_wait"

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete", "hasBusyOverlay": False}

    async def fail_rush():
        raise AssertionError("rush should not run before sale time is configured")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_seconds_until_sale_takeover", lambda: None)
    monkeypatch.setattr(bot, "_rush_quick_booking_takeover", fail_rush)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)
    monkeypatch.setattr(bot, "_report", lambda msg: reports.append(msg))

    assert await bot.run_takeover() is False
    assert any("Sale time" in msg for msg in reports)


@pytest.mark.asyncio
async def test_takeover_waits_until_sale_time_before_executing(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    events = []
    sale_waits = []

    base_now = bot._now_local()
    now_values = iter(
        [
            base_now,
            base_now + timedelta(seconds=31),
            base_now + timedelta(seconds=31),
            base_now + timedelta(seconds=31),
        ]
    )

    states = iter(["pre_sale_wait", "pre_sale_wait", "ticket_type", "checkout"])

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete", "hasBusyOverlay": False}

    async def fake_sleep_until_sale(seconds_left):
        sale_waits.append(seconds_left)

    async def fake_rush():
        events.append("rush")
        return True

    async def fake_select_ticket_type():
        events.append("ticket_type")
        return True

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return "checkout"

    async def fake_checkout():
        events.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_sleep_until_sale_window_takeover", fake_sleep_until_sale)
    monkeypatch.setattr(bot, "_rush_quick_booking_takeover", fake_rush)
    monkeypatch.setattr(bot, "_select_ticket_type_takeover", fake_select_ticket_type)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr(bot, "_parse_sale_time_takeover", lambda: base_now + timedelta(seconds=30))
    monkeypatch.setattr(bot, "_now_local", lambda: next(now_values))
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert sale_waits
    assert events == ["rush", "ticket_type", "wait:ticket_type", "checkout"]


def test_takeover_parse_sale_time_from_config_string(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.event.sale_time = "2026/04/08 11:00"

    parsed = bot._parse_sale_time_takeover()

    assert parsed is not None
    assert parsed.strftime("%Y-%m-%d %H:%M") == "2026-04-08 11:00"
    assert parsed.tzinfo is not None


@pytest.mark.asyncio
async def test_takeover_click_best_button_prefers_exact_showtime_button(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    async def fake_read_controls():
        return {
            "buttons": [
                {"label": "16:40", "x": 320, "y": 420},
                {"label": "16:40 sold out", "x": 320, "y": 560},
            ],
            "selects": [],
            "checkboxes": [],
            "viewportHeight": 900,
        }

    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)

    result = await bot._click_best_button_takeover(["16:40"])

    assert result is True
    assert bot.cdp.clicks == [(320.0, 420.0)]


@pytest.mark.asyncio
async def test_takeover_booking_landing_prefers_exact_action_card(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP(
        {
            "selects": [],
            "checkboxes": [{"label": "agree to terms", "checked": False, "x": 100, "y": 200}],
            "buttons": [
                {"label": "前往訂票", "x": 460, "y": 420},
                {"label": "前往訂票 待確認", "x": 460, "y": 760},
            ],
            "viewportHeight": 900,
        }
    )

    result = await bot._enter_booking_flow_takeover()

    assert result is True
    assert bot.cdp.clicks[0] == (100.0, 200.0)
    assert bot.cdp.clicks[1] == (460.0, 420.0)


@pytest.mark.asyncio
async def test_takeover_pre_sale_to_checkout_flow_uses_booking_landing_and_seat_selection(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    states = iter(["pre_sale_wait", "booking_landing", "ticket_type", "seat_selection", "checkout"])
    events = []

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        return next(states)

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete", "hasBusyOverlay": False}

    async def fake_rush():
        events.append("showtime")
        return True

    async def fake_enter_booking():
        events.append("booking_landing")
        return True

    async def fake_select_ticket_type():
        events.append("ticket_type")
        return True

    async def fake_grab_seats():
        events.append("seat_selection")
        return ["C3", "C4"]

    async def fake_wait_for_state_change(previous_state, timeout=4.0, poll=0.06):
        events.append(f"wait:{previous_state}")
        return {
            "booking_landing": "ticket_type",
            "ticket_type": "seat_selection",
            "seat_selection": "checkout",
        }[previous_state]

    async def fake_checkout():
        events.append("checkout")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr(bot, "_seconds_until_sale_takeover", lambda: 0.0)
    monkeypatch.setattr(bot, "_rush_quick_booking_takeover", fake_rush)
    monkeypatch.setattr(bot, "_enter_booking_flow_takeover", fake_enter_booking)
    monkeypatch.setattr(bot, "_select_ticket_type_takeover", fake_select_ticket_type)
    monkeypatch.setattr(bot, "_grab_seats_takeover", fake_grab_seats)
    monkeypatch.setattr(bot, "_wait_for_state_change_takeover", fake_wait_for_state_change)
    monkeypatch.setattr(bot, "_handle_checkout_takeover", fake_checkout)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    assert await bot.run_takeover() is True
    assert events == [
        "showtime",
        "booking_landing",
        "wait:booking_landing",
        "ticket_type",
        "wait:ticket_type",
        "seat_selection",
        "wait:seat_selection",
        "checkout",
    ]


@pytest.mark.asyncio
async def test_takeover_wait_ticket_continue_ready_retries_until_enabled(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})

    snapshots = iter(
        [
            {
                "buttons": [{"label": "繼續", "disabled": True, "pointerEvents": "auto", "x": 1000, "y": 1100}],
                "selects": [],
                "checkboxes": [],
                "viewportHeight": 900,
            },
            {
                "buttons": [{"label": "繼續", "disabled": False, "pointerEvents": "auto", "x": 1000, "y": 1100}],
                "selects": [],
                "checkboxes": [],
                "viewportHeight": 900,
            },
        ]
    )

    async def fake_detect_state():
        return "ticket_type"

    async def fake_read_controls():
        try:
            return next(snapshots)
        except StopIteration:
            return {
                "buttons": [{"label": "繼續", "disabled": False, "pointerEvents": "auto", "x": 1000, "y": 1100}],
                "selects": [],
                "checkboxes": [],
                "viewportHeight": 900,
            }

    async def fake_read_activity():
        return {"busy": False, "readyState": "complete"}

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr(bot, "_read_ticket_controls_takeover", fake_read_controls)
    monkeypatch.setattr(bot, "_read_page_activity_takeover", fake_read_activity)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)

    result = await bot._wait_ticket_continue_ready_takeover(timeout=0.2, poll=0.01)
    assert result is True


@pytest.mark.asyncio
async def test_takeover_reports_order_history_page(monkeypatch):
    bot = _make_bot(monkeypatch, takeover=True)
    bot.cdp = _ConstantCDP({})
    reports = []

    states = iter(["order_history", "order_history"])

    async def fake_start_browser():
        bot.cdp = _ConstantCDP({})

    async def fake_detect_state():
        bot.request_stop()
        return next(states)

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(bot, "start_browser", fake_start_browser)
    monkeypatch.setattr(bot, "_detect_takeover_state", fake_detect_state)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.asyncio.sleep", no_sleep)
    monkeypatch.setattr(bot, "_report", lambda msg: reports.append(msg))

    assert await bot.run_takeover() is False
    assert any("PaymentHistory" in msg for msg in reports)
