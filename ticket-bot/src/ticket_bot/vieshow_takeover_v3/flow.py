"""Dedicated VieShow takeover flow (v3)."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any, Callable

from ticket_bot.browser.cdp_takeover import CDPError, CDPTakeoverEngine
from ticket_bot.config import AppConfig, EventConfig, SessionConfig

logger = logging.getLogger(__name__)


READ_PAGE_STATE_JS = """
(() => {
  const txt = String((document.body && (document.body.innerText || document.body.textContent)) || "").replace(/\\s+/g, " ").trim();
  const url = String(location.href || "");
  let state = "unknown";
  if (/\\/LiveTicketT2\\/Home\\/OrderConfirm/i.test(url) || /(OrderConfirm|\\u8acb\\u9078\\u64c7\\u4ed8\\u6b3e\\u65b9\\u5f0f)/i.test(txt)) state = "order_confirm";
  else if (/\\/LiveTicketT2\\/Home\\/SelectSeats/i.test(url) || /(screen|seat|\\u9078\\u64c7\\u5ea7\\u4f4d)/i.test(txt)) state = "seat_selection";
  else if (/\\/LiveTicketT2\\//i.test(url) || /(ticket|\\u4e00\\u822c\\u7968\\u7a2e|\\u5168\\u7968)/i.test(txt)) state = "ticket_type";
  else if (/\\/vsTicketing\\/ticketing\\/booking\\.aspx/i.test(url)) {
    const hasRules = /(\\u8a02\\u7968\\u53ca\\u53d6\\u7968\\u898f\\u5b9a|\\u9000\\u63db\\u7968\\u898f\\u5b9a)/.test(txt) ||
      !!document.querySelector("input[type='checkbox']");
    state = hasRules ? "booking_rules" : "booking_option";
  } else if (/\\/vsTicketing\\/ticketing\\/ticket\\.aspx/i.test(url)) state = "ticket_showtime";
  return {
    state,
    url,
    readyState: String(document.readyState || ""),
    hasBusyOverlay: !!document.querySelector(".loading,.loader,.spinner,.blockUI,[aria-busy='true'],.swal2-container"),
  };
})()
"""


READ_SHOWTIME_OPTIONS_JS = """
(() => {
  const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  const out = [];
  const seen = new Set();
  for (const n of document.querySelectorAll("a,button,input[type='button'],input[type='submit'],[onclick],[role='button']")) {
    if (!vis(n) || n.disabled || n.getAttribute("aria-disabled") === "true") continue;
    const t = txt(n);
    if (!/^\\d{1,2}:\\d{2}$/.test(t)) continue;
    const r = n.getBoundingClientRect();
    const key = `${t}|${Math.round(r.top)}|${Math.round(r.left)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      optionId: String(out.length),
      text: t,
      value: t,
      label: t,
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      top: r.top,
      left: r.left,
      selected: /active|selected|current/i.test(String(n.className || "")),
    });
  }
  out.sort((a, b) => (a.top - b.top) || (a.left - b.left));
  return out;
})()
"""


READ_CHECKBOXES_JS = """
(() => {
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  return Array.from(document.querySelectorAll("input[type='checkbox']")).filter(vis).map((n, i) => {
    const r = n.getBoundingClientRect();
    return { index: i, checked: !!n.checked, x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
})()
"""


READ_TICKET_SELECTS_JS = """
(() => {
  const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  return Array.from(document.querySelectorAll("select")).map((n, domIndex) => ({
    domIndex,
    rowText: txt(n.closest("tr")),
    label: txt(n.closest("tr,li,div,form") || n.parentElement || n),
    visible: vis(n),
    selectedIndex: Number(n.selectedIndex || 0),
    options: Array.from(n.options || []).map((o, i) => ({ index: i, text: txt(o), value: String(o.value || "") })),
  }));
})()
"""


READ_BOOKING_OPTION_CARD_JS = """
(() => {
  const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width < 90 || r.height < 40) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  const include = /(general|bank privilege|\\u7dda\\u4e0a\\u5373\\u6642\\u4ed8\\u6b3e|\\u4e00\\u822c|\\u9280\\u884c)/i;
  const exclude = /(corporate|movie money|\\u5718\\u9ad4|\\u611b\\u5fc3|\\u656c\\u8001|\\u514d\\u8cbb\\u514c\\u63db)/i;
  let best = null;
  for (const n of document.querySelectorAll("a,button,div,li")) {
    if (!vis(n)) continue;
    const t = txt(n);
    if (!include.test(t) || exclude.test(t)) continue;
    const r = n.getBoundingClientRect();
    const item = {
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      top: r.top,
      left: r.left,
      text: t
    };
    if (!best || item.top < best.top || (item.top === best.top && item.left < best.left)) {
      best = item;
    }
  }
  return best;
})()
"""


READ_PRIMARY_CONTINUE_BUTTON_JS = """
(() => {
  const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  const include = /(\\u7e7c\\u7e8c|\\u4e0b\\u4e00\\u6b65|\\u524d\\u5f80\\u8a02\\u7968|continue|next)/i;
  const exclude = /(\\u53d6\\u6d88|\\u8fd4\\u56de|\\u4e0a\\u4e00\\u6b65|cancel|back)/i;
  const inViewport = (n) => {
    const r = n.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
  };
  const good = [];
  const fallback = [];
  for (const n of document.querySelectorAll("button,a,input[type='button'],input[type='submit']")) {
    if (!vis(n)) continue;
    if (n.disabled || n.getAttribute("aria-disabled") === "true" || /disabled/.test(String(n.className || ""))) continue;
    const t = txt(n);
    const r = n.getBoundingClientRect();
    const item = { node: n, label: t, top: r.top, left: r.left, inViewport: inViewport(n) };
    if (!exclude.test(t) && include.test(t)) good.push(item);
    else if (!exclude.test(t)) fallback.push(item);
  }
  const rank = (arr) => arr.sort((a, b) => {
    if (a.inViewport !== b.inViewport) return a.inViewport ? 1 : -1;
    if (a.top !== b.top) return a.top - b.top;
    return a.left - b.left;
  });
  rank(good);
  rank(fallback);
  const chosen = good.length ? good[good.length - 1] : (fallback.length ? fallback[fallback.length - 1] : null);
  if (!chosen || !chosen.node) return null;
  chosen.node.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  const rr = chosen.node.getBoundingClientRect();
  if (rr.width <= 0 || rr.height <= 0) return null;
  if (chosen.node.disabled || chosen.node.getAttribute("aria-disabled") === "true") return null;
  if (/disabled/.test(String(chosen.node.className || ""))) return null;
  return { label: chosen.label, x: rr.left + rr.width / 2, y: rr.top + rr.height / 2, top: rr.top, left: rr.left };
})()
"""


CLICK_PRIMARY_CONTINUE_JS = """
(() => {
  const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
  const vis = (n) => {
    if (!n) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = window.getComputedStyle(n);
    if (!s) return false;
    return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
  };
  const include = /(\\u7e7c\\u7e8c|\\u4e0b\\u4e00\\u6b65|\\u524d\\u5f80\\u8a02\\u7968|continue|next)/i;
  const exclude = /(\\u53d6\\u6d88|\\u8fd4\\u56de|\\u4e0a\\u4e00\\u6b65|cancel|back)/i;
  let best = null;
  for (const n of document.querySelectorAll("button,a,input[type='button'],input[type='submit']")) {
    if (!vis(n)) continue;
    if (n.disabled || n.getAttribute("aria-disabled") === "true" || /disabled/.test(String(n.className || ""))) continue;
    const t = txt(n);
    if (exclude.test(t) || !include.test(t)) continue;
    const r = n.getBoundingClientRect();
    const score = r.top * 100000 + r.left;
    if (!best || score > best.score) best = { node: n, score };
  }
  if (!best || !best.node) return false;
  best.node.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  best.node.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window }));
  best.node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
  best.node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
  best.node.click();
  return true;
})()
"""


def _safe_int(raw: str) -> int | None:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_sale_time(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _contains_any(text: str, words: list[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


async def read_showtime_options(*, cdp_url: str, page_url_substring: str) -> list[dict[str, Any]]:
    cdp = CDPTakeoverEngine()
    try:
        await cdp.connect(cdp_url=cdp_url, page_url_substring=page_url_substring)
        payload = await cdp.evaluate(READ_SHOWTIME_OPTIONS_JS)
        return list(payload or [])
    finally:
        await cdp.close()


class VieShowTakeoverV3:
    """Fixed takeover state machine for VieShow booking."""

    def __init__(self, config: AppConfig, event: EventConfig, session: SessionConfig | None = None):
        self.config = config
        self.event = event
        self.session = session
        self.cdp = CDPTakeoverEngine()
        self._status_callback: Callable[[str], None] | None = None
        self._stop_requested = False
        self._last_mouse = (140.0, 120.0)
        self._last_state = ""
        self._last_wait_second: int | None = None
        self._sale_time_dt = _parse_sale_time(event.sale_time)
        self._ticket_qty_ready = False
        self.last_success_info = ""

    def set_status_callback(self, callback: Callable[[str], None]) -> None:
        self._status_callback = callback

    def request_stop(self) -> None:
        self._stop_requested = True

    async def close(self) -> None:
        await self.cdp.close()

    def _report(self, message: str) -> None:
        logger.info("[vieshow-v3] %s", message)
        if self._status_callback:
            self._status_callback(message)

    async def _read_state(self) -> dict[str, Any]:
        payload = await self.cdp.evaluate(READ_PAGE_STATE_JS)
        if isinstance(payload, dict):
            return payload
        return {"state": "unknown", "url": "", "readyState": "", "hasBusyOverlay": False}

    @staticmethod
    def _state_from_url(url: str, js_state: str) -> str:
        lower = str(url or "").lower()
        if "/vsticketing/ticketing/ticket.aspx" in lower:
            return "ticket_showtime"
        if "/vsticketing/ticketing/booking.aspx" in lower:
            return js_state if js_state in {"booking_rules", "booking_option"} else "booking_option"
        if "/livetickett2/home/selectseats" in lower:
            return "seat_selection"
        if "/livetickett2/home/orderconfirm" in lower:
            return "order_confirm"
        if "/livetickett2/" in lower:
            return "ticket_type"
        return js_state or "unknown"

    async def _wait_page_ready(self, timeout: float = 1.8) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._stop_requested and asyncio.get_running_loop().time() < deadline:
            info = await self._read_state()
            if str(info.get("readyState") or "") in {"interactive", "complete"} and not bool(info.get("hasBusyOverlay")):
                return
            await asyncio.sleep(0.03)

    async def _move_and_click(self, x: float, y: float) -> None:
        try:
            await self.cdp.human_mouse_move(self._last_mouse, (x, y), duration_ms=55, steps=5)
        except Exception:
            await self.cdp.dispatch_mouse_event("mouseMoved", x, y, button="none", click_count=0)
        await self.cdp.dispatch_click(x, y)
        self._last_mouse = (x, y)

    async def _click_button(
        self,
        include_keywords: list[str],
        *,
        exclude_keywords: list[str] | None = None,
        prefer_top: bool = False,
    ) -> bool:
        payload = await self.cdp.evaluate(
            """
            (() => {
              const txt = (n) => String((n && (n.innerText || n.textContent || n.value)) || "").replace(/\\s+/g, " ").trim();
              const vis = (n) => {
                if (!n) return false;
                const r = n.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return false;
                const s = window.getComputedStyle(n);
                if (!s) return false;
                return s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || "1") >= 0.05;
              };
              const out = [];
              for (const n of document.querySelectorAll("button,a,input[type='button'],input[type='submit'],[onclick],[role='button']")) {
                if (!vis(n)) continue;
                const r = n.getBoundingClientRect();
                const s = window.getComputedStyle(n);
                out.push({
                  label: txt(n),
                  x: r.left + r.width / 2,
                  y: r.top + r.height / 2,
                  top: r.top,
                  left: r.left,
                  disabled: !!(n.disabled || n.getAttribute("aria-disabled") === "true" || /disabled/.test(String(n.className || ""))),
                  pointerEvents: String((s && s.pointerEvents) || "")
                });
              }
              return out;
            })()
            """
        )
        buttons = list(payload or [])
        include = [value.lower() for value in include_keywords]
        exclude = [value.lower() for value in (exclude_keywords or [])]
        candidates: list[tuple[tuple[int, int, float, float], dict[str, Any]]] = []

        for button in buttons:
            label = str(button.get("label") or "").strip().lower()
            if not label:
                continue
            if bool(button.get("disabled")):
                continue
            if str(button.get("pointerEvents") or "").strip().lower() == "none":
                continue
            if exclude and any(word in label for word in exclude):
                continue
            matches = [word for word in include if word in label]
            if not matches:
                continue
            score = (
                1 if any(word == label for word in matches) else 0,
                max(len(word) for word in matches),
                -float(button.get("y") or 0) if prefer_top else float(button.get("y") or 0),
                float(button.get("x") or 0),
            )
            candidates.append((score, button))

        if not candidates:
            return False
        _, chosen = max(candidates, key=lambda item: item[0])
        await self._move_and_click(float(chosen["x"]), float(chosen["y"]))
        return True

    async def _close_dialog_if_present(self) -> bool:
        try:
            await self.cdp._send("Page.handleJavaScriptDialog", {"accept": True})  # noqa: SLF001
            self._report("Detected browser dialog, accepted.")
            return True
        except CDPError:
            return False

    async def _wait_for_sale_window(self) -> bool:
        if self._sale_time_dt is None:
            return True
        seconds_left = (self._sale_time_dt - datetime.now()).total_seconds()
        if seconds_left <= 0:
            self._last_wait_second = None
            return True
        bucket = int(seconds_left)
        if self._last_wait_second != bucket:
            self._last_wait_second = bucket
            self._report(f"Waiting for sale window: {bucket}s")
        await asyncio.sleep(min(0.5, max(0.03, seconds_left - 0.15)))
        return False

    async def _click_showtime(self) -> bool:
        options = list(await self.cdp.evaluate(READ_SHOWTIME_OPTIONS_JS) or [])
        if not options:
            self._report("No showtime buttons found on ticket.aspx.")
            return False

        preferred_id = str(self.event.presale_code or "").strip()
        preferred_text = str(self.config.vieshow.showtime_keyword or self.event.date_keyword or "").strip().lower()
        target = None
        if preferred_id:
            target = next((o for o in options if str(o.get("optionId") or "").strip() == preferred_id), None)
        if not target and preferred_text:
            target = next(
                (o for o in options if preferred_text in str(o.get("text") or "").strip().lower()),
                None,
            )
        if not target:
            target = next((o for o in options if bool(o.get("selected"))), None) or options[0]

        await self._move_and_click(float(target["x"]), float(target["y"]))
        self._report(f"Clicked showtime: {target.get('text') or '(unknown)'}")
        return True

    async def _click_booking_option_top(self) -> bool:
        clicked = await self._click_button(
            ["general", "bank privilege", "\u7dda\u4e0a\u5373\u6642\u4ed8\u6b3e", "\u4e00\u822c", "\u9280\u884c"],
            exclude_keywords=["corporate", "movie money", "\u5718\u9ad4", "\u611b\u5fc3", "\u656c\u8001"],
            prefer_top=True,
        )
        if clicked:
            self._report("Selected booking payment option (top card).")
            return True

        payload = await self.cdp.evaluate(READ_BOOKING_OPTION_CARD_JS)
        if isinstance(payload, dict) and "x" in payload and "y" in payload:
            await self._move_and_click(float(payload["x"]), float(payload["y"]))
            self._report("Selected booking payment option by card-text fallback.")
            return True
        return False

    async def _has_rules_checkbox(self) -> bool:
        payload = await self.cdp.evaluate(READ_CHECKBOXES_JS)
        return bool(list(payload or []))

    async def _click_rules_agree(self) -> bool:
        payload = await self.cdp.evaluate(READ_CHECKBOXES_JS)
        checkboxes = list(payload or [])
        if not checkboxes:
            self._report("Rules checkbox not found.")
            return False
        if any(bool(item.get("checked")) for item in checkboxes):
            return True

        first = checkboxes[0]
        try:
            await self._move_and_click(float(first["x"]), float(first["y"]))
            await asyncio.sleep(0.04)
        except Exception:
            pass
        verify = await self.cdp.evaluate(READ_CHECKBOXES_JS)
        if any(bool(item.get("checked")) for item in list(verify or [])):
            self._report("Checked booking agreement checkbox.")
            return True

        forced = await self.cdp.evaluate(
            """
            (() => {
              const node = document.querySelector("input[type='checkbox']");
              if (!node) return false;
              node.checked = true;
              node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
              node.dispatchEvent(new Event('input', { bubbles: true }));
              node.dispatchEvent(new Event('change', { bubbles: true }));
              return !!node.checked;
            })()
            """
        )
        if bool(forced):
            self._report("Checked booking agreement checkbox by JS fallback.")
            return True
        self._report("Failed to check booking agreement checkbox.")
        return False

    async def _click_primary_continue(self) -> bool:
        payload = await self.cdp.evaluate(READ_PRIMARY_CONTINUE_BUTTON_JS)
        if isinstance(payload, dict) and "x" in payload and "y" in payload:
            await self._move_and_click(float(payload["x"]), float(payload["y"]))
            self._report("Clicked primary continue button.")
            return True
        try:
            forced = await self.cdp.evaluate(CLICK_PRIMARY_CONTINUE_JS)
        except Exception:
            forced = False
        if bool(forced):
            self._report("Clicked primary continue button by JS fallback.")
            return True
        return False

    async def _click_rules_continue(self) -> bool:
        await self.cdp.evaluate("window.scrollTo(0, document.body.scrollHeight); true;")
        for _ in range(20):
            if await self._click_primary_continue():
                return True
            await asyncio.sleep(0.05)
        self._report("Rules continue button not ready.")
        return False

    async def _set_select_index(self, dom_index: int, target_index: int) -> bool:
        changed = await self.cdp.evaluate(
            f"""
            (() => {{
              const all = Array.from(document.querySelectorAll('select'));
              if ({dom_index} < 0 || {dom_index} >= all.length) return false;
              const select = all[{dom_index}];
              if ({target_index} < 0 || {target_index} >= select.options.length) return false;
              select.selectedIndex = {target_index};
              select.dispatchEvent(new Event('input', {{ bubbles: true }}));
              select.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return true;
            }})()
            """
        )
        return bool(changed)

    async def _select_general_full_ticket_count(self) -> bool:
        await self._click_button(["general", "ticket", "\u4e00\u822c\u7968\u7a2e"], prefer_top=True)
        await asyncio.sleep(0.02)

        selects = list(await self.cdp.evaluate(READ_TICKET_SELECTS_JS) or [])
        if not selects:
            self._report("No ticket quantity selects found.")
            return False

        include_full = ["\u5168\u7968", "full"]
        include_general = ["\u4e00\u822c\u7968\u7a2e", "general"]
        exclude_words = [
            "\u512a\u5f85",
            "\u5b78\u751f",
            "\u8ecd\u8b66",
            "\u611b\u5fc3",
            "\u656c\u8001",
            "\u6703\u54e1",
            "\u5957\u7968",
            "bank",
            "privilege",
            "discount",
            "student",
            "senior",
            "love",
            "ishow",
        ]

        target: dict[str, Any] | None = None
        best_score = -10_000
        for item in selects:
            text_blob = " ".join([str(item.get("rowText") or ""), str(item.get("label") or "")]).lower()
            score = 0
            if _contains_any(text_blob, include_full):
                score += 20
            if _contains_any(text_blob, include_general):
                score += 12
            if any(word in text_blob for word in exclude_words):
                score -= 14
            if bool(item.get("visible")):
                score += 2
            score -= int(item.get("domIndex") or 0)
            if score > best_score:
                best_score = score
                target = item

        if not target or best_score < 10:
            self._report("Could not identify full-ticket dropdown.")
            return False

        desired_count = max(1, int(self.event.ticket_count or 2))
        target_index = None
        for option in list(target.get("options") or []):
            if _safe_int(str(option.get("text") or "")) == desired_count or _safe_int(str(option.get("value") or "")) == desired_count:
                target_index = int(option.get("index") or 0)
                break
        if target_index is None:
            self._report(f"Ticket count option not found: {desired_count}")
            return False

        dom_index = int(target.get("domIndex") or -1)
        if dom_index < 0:
            self._report("Invalid dropdown index for full ticket.")
            return False

        if int(target.get("selectedIndex") or -1) == target_index:
            if not self._ticket_qty_ready:
                self._report(f"Full ticket quantity already set: {desired_count}")
            self._ticket_qty_ready = True
            return True

        changed = await self._set_select_index(dom_index, target_index)
        if not changed:
            self._report("Failed to set full ticket quantity.")
            return False

        self._ticket_qty_ready = True
        self._report(f"Selected full ticket quantity: {desired_count}")
        return True

    async def _run_state_machine(self) -> bool:
        while not self._stop_requested:
            await self._wait_page_ready(timeout=1.5)
            info = await self._read_state()
            state = self._state_from_url(str(info.get("url") or ""), str(info.get("state") or "unknown"))

            if state != self._last_state:
                self._last_state = state
                self._report(f"State: {state}")
            if state in {"ticket_showtime", "booking_option", "booking_rules"}:
                self._ticket_qty_ready = False

            if state == "ticket_showtime":
                if not await self._wait_for_sale_window():
                    continue
                await self._click_showtime()
                await asyncio.sleep(0.02)
                await self._close_dialog_if_present()
                await asyncio.sleep(0.02)
                continue

            if state == "booking_option":
                if await self._has_rules_checkbox():
                    self._report("Detected rules controls on booking.aspx, switching to rules step.")
                    await self._click_rules_agree()
                    await self._click_rules_continue()
                    await asyncio.sleep(0.03)
                    continue

                clicked = await self._click_booking_option_top()
                if not clicked:
                    await asyncio.sleep(0.06)
                    continue

                for _ in range(15):
                    await asyncio.sleep(0.04)
                    if await self._has_rules_checkbox():
                        self._report("Rules page appeared after payment option click.")
                        await self._click_rules_agree()
                        await self._click_rules_continue()
                        break
                await asyncio.sleep(0.03)
                continue

            if state == "booking_rules":
                await self._click_rules_agree()
                await self._click_rules_continue()
                await asyncio.sleep(0.03)
                continue

            if state == "ticket_type":
                done = self._ticket_qty_ready
                if not done:
                    done = await self._select_general_full_ticket_count()
                if done:
                    clicked = False
                    for _ in range(10):
                        clicked = await self._click_primary_continue()
                        if clicked:
                            break
                        await asyncio.sleep(0.06)
                    if not clicked:
                        self._report("Ticket type page: continue button not clickable yet.")
                await asyncio.sleep(0.05)
                continue

            if state == "seat_selection":
                clicked = False
                for _ in range(10):
                    clicked = await self._click_primary_continue()
                    if clicked:
                        break
                    await asyncio.sleep(0.06)
                if not clicked:
                    self._report("Seat page: continue button not clickable yet.")
                await asyncio.sleep(0.05)
                continue

            if state == "order_confirm":
                self.last_success_info = "Reached OrderConfirm"
                self._report("Reached OrderConfirm page, takeover flow completed.")
                return True

            await self._close_dialog_if_present()
            await asyncio.sleep(0.08)

        return False

    async def run(self) -> bool:
        takeover = self.config.vieshow.takeover
        cdp_url = takeover.resolved_cdp_url()
        page_filter = takeover.page_url_substring or "vscinemas.com.tw"
        self._report(f"Attach CDP: {cdp_url}")
        target = await self.cdp.connect(cdp_url=cdp_url, page_url_substring=page_filter)
        self._report(f"Attached tab: {target.url}")
        try:
            await self.cdp._send("Page.enable", {})  # noqa: SLF001
        except CDPError:
            pass
        return await self._run_state_machine()


__all__ = ["VieShowTakeoverV3", "read_showtime_options"]
