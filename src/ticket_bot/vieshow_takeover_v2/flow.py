"""Dedicated VieShow takeover flow (v2) with fixed URL-state transitions."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import re
from typing import Any, Callable

from ticket_bot.browser.cdp_takeover import CDPError, CDPTakeoverEngine
from ticket_bot.config import AppConfig, EventConfig, SessionConfig

logger = logging.getLogger(__name__)


READ_PAGE_STATE_JS = """
(() => {
  const url = String(location.href || "");
  const bodyText = String((document.body && document.body.innerText) || "").replace(/\\s+/g, " ").trim();
  const hasSwal = !!document.querySelector(".swal2-popup, .swal2-container");
  const hasRulesText = /\\u8a02\\u7968\\u53ca\\u53d6\\u7968\\u898f\\u5b9a/.test(bodyText);
  const hasOptionText = /\\u7dda\\u4e0a\\u5373\\u6642\\u4ed8\\u6b3e|GENERAL\\s*\\/\\s*BANK\\s*PRIVILEGE|CORPORATE\\s*MOVIE\\s*MONEY/i.test(bodyText);
  const hasTicketTypeText = /\\u4e00\\u822c\\u7968\\u7a2e|\\u5168\\u7968|\\u9078\\u64c7\\u96fb\\u5f71\\u7968|ticket/i.test(bodyText);
  const hasSeatText = /\\u9078\\u64c7\\u5ea7\\u4f4d|\\u9280\\u5e55|Screen|seat/i.test(bodyText);
  const hasOrderConfirmText = /\\u8acb\\u8f38\\u5165\\u8cfc\\u8cb7\\u4eba\\u8cc7\\u8a0a|OrderConfirm|\\u8acb\\u9078\\u64c7\\u4ed8\\u6b3e\\u65b9\\u5f0f/i.test(bodyText);
  const hasBusyOverlay = !!document.querySelector(".loading, .loader, .spinner, .blockUI, [aria-busy='true']");
  const readyState = String(document.readyState || "");

  let state = "unknown";
  if (/\\/LiveTicketT2\\/Home\\/OrderConfirm/i.test(url) || hasOrderConfirmText) {
    state = "order_confirm";
  } else if (/\\/LiveTicketT2\\/Home\\/SelectSeats/i.test(url) || hasSeatText) {
    state = "seat_selection";
  } else if (/\\/LiveTicketT2\\/\\?agree=on/i.test(url) || hasTicketTypeText) {
    state = "ticket_type";
  } else if (/vsTicketing\\/ticketing\\/booking\\.aspx/i.test(url)) {
    if (hasRulesText) {
      state = "booking_rules";
    } else if (hasOptionText) {
      state = "booking_option";
    } else {
      state = "booking_unknown";
    }
  } else if (/vsTicketing\\/ticketing\\/ticket\\.aspx/i.test(url)) {
    state = "ticket_showtime";
  }

  return {
    state,
    url,
    hasSwal,
    readyState,
    hasBusyOverlay,
  };
})()
"""


READ_SHOWTIME_OPTIONS_JS = """
(() => {
  const isVisible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") < 0.05) return false;
    return true;
  };
  const textOf = (node) => String((node && (node.innerText || node.textContent || node.value)) || "").replace(/\\s+/g, " ").trim();
  const center = (node) => {
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, left: r.left };
  };
  const candidates = [];
  const seen = new Set();
  const nodes = document.querySelectorAll("a,button,input[type='button'],input[type='submit'],[onclick],[role='button']");
  for (const node of nodes) {
    if (!isVisible(node)) continue;
    if (node.disabled || node.getAttribute("aria-disabled") === "true") continue;
    const text = textOf(node);
    if (!/^\\d{1,2}:\\d{2}$/.test(text)) continue;
    const point = center(node);
    const key = `${text}|${Math.round(point.top)}|${Math.round(point.left)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push({
      optionId: String(candidates.length),
      text,
      label: text,
      x: point.x,
      y: point.y,
      top: point.top,
      left: point.left,
    });
  }
  candidates.sort((a, b) => (a.top - b.top) || (a.left - b.left));
  return candidates.map((item, index) => ({
    optionId: String(index),
    text: item.text,
    label: item.label,
    x: item.x,
    y: item.y,
    top: item.top,
    left: item.left,
  }));
})()
"""


READ_CLICKABLES_JS = """
(() => {
  const isVisible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") < 0.05) return false;
    return true;
  };
  const textOf = (node) => String((node && (node.innerText || node.textContent || node.value)) || "").replace(/\\s+/g, " ").trim();
  const center = (node) => {
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, left: r.left };
  };
  const nodes = document.querySelectorAll("button,a,input[type='button'],input[type='submit'],[onclick],[role='button']");
  const out = [];
  for (const node of nodes) {
    if (!isVisible(node)) continue;
    const label = textOf(node);
    if (!label) continue;
    const point = center(node);
    const style = window.getComputedStyle(node);
    out.push({
      label,
      x: point.x,
      y: point.y,
      top: point.top,
      left: point.left,
      disabled: !!(node.disabled || node.getAttribute("aria-disabled") === "true" || /disabled/.test(String(node.className || ""))),
      pointerEvents: String((style && style.pointerEvents) || ""),
    });
  }
  return out;
})()
"""


READ_CHECKBOXES_JS = """
(() => {
  const isVisible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") < 0.05) return false;
    return true;
  };
  const textOf = (node) => String((node && (node.innerText || node.textContent || node.value)) || "").replace(/\\s+/g, " ").trim();
  const center = (node) => {
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };
  return Array.from(document.querySelectorAll("input[type='checkbox']")).filter(isVisible).map((node, index) => {
    const scope = node.closest("label,tr,li,div,form") || node.parentElement || node;
    const point = center(node);
    return {
      index,
      checked: !!node.checked,
      label: textOf(scope),
      x: point.x,
      y: point.y,
    };
  });
})()
"""


READ_TICKET_SELECTS_JS = """
(() => {
  const isVisible = (node) => {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity || "1") < 0.05) return false;
    return true;
  };
  const textOf = (node) => String((node && (node.innerText || node.textContent || node.value)) || "").replace(/\\s+/g, " ").trim();
  const center = (node) => {
    const r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };
  const readHeaders = (node) => {
    const headers = [];
    let cursor = node.parentElement;
    while (cursor && headers.length < 6) {
      const h = cursor.querySelector("button, a, .panel-title, .accordion-toggle, .card-header, h1, h2, h3, h4");
      const t = textOf(h);
      if (t) headers.push(t);
      cursor = cursor.parentElement;
    }
    return Array.from(new Set(headers));
  };
  return Array.from(document.querySelectorAll("select")).map((node, domIndex) => {
    const row = node.closest("tr");
    const scope = node.closest("tr, li, .ticket, .ticket-type, .ticketQty, .quantity, div, form") || node.parentElement || node;
    const point = center(node);
    return {
      domIndex,
      name: String(node.name || node.id || ""),
      rowText: textOf(row),
      label: textOf(scope),
      headers: readHeaders(node),
      visible: isVisible(node),
      selectedIndex: Number(node.selectedIndex || 0),
      x: point.x,
      y: point.y,
      options: Array.from(node.options || []).map((opt, index) => ({
        index,
        text: textOf(opt),
        value: String(opt.value || ""),
      })),
    };
  });
})()
"""


def _safe_int(text: str) -> int | None:
    digits = "".join(ch for ch in text if ch.isdigit())
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
    formats = [
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


async def read_showtime_options(*, cdp_url: str, page_url_substring: str) -> list[dict[str, Any]]:
    """Attach to Chrome and read showtime choices from ticket.aspx page."""
    cdp = CDPTakeoverEngine()
    try:
        await cdp.connect(cdp_url=cdp_url, page_url_substring=page_url_substring)
        payload = await cdp.evaluate(READ_SHOWTIME_OPTIONS_JS)
        return list(payload or [])
    finally:
        await cdp.close()


class VieShowTakeoverV2:
    """Fixed takeover state machine for VieShow URL sequence."""

    def __init__(self, config: AppConfig, event: EventConfig, session: SessionConfig | None = None):
        self.config = config
        self.event = event
        self.session = session
        self.cdp = CDPTakeoverEngine()
        self.last_success_info = ""
        self._status_callback: Callable[[str], None] | None = None
        self._stop_requested = False
        self._last_mouse = (160.0, 140.0)
        self._last_state = ""

    def set_status_callback(self, callback: Callable[[str], None]) -> None:
        self._status_callback = callback

    def request_stop(self) -> None:
        self._stop_requested = True

    async def close(self) -> None:
        await self.cdp.close()

    def _report(self, message: str) -> None:
        logger.info("[vieshow-v2] %s", message)
        if self._status_callback:
            self._status_callback(message)

    async def _move_and_click(self, x: float, y: float) -> None:
        try:
            await self.cdp.human_mouse_move(self._last_mouse, (x, y), duration_ms=110, steps=8)
        except Exception:
            await self.cdp.dispatch_mouse_event("mouseMoved", x, y, button="none", click_count=0)
        await self.cdp.dispatch_click(x, y)
        self._last_mouse = (x, y)

    async def _read_state(self) -> dict[str, Any]:
        payload = await self.cdp.evaluate(READ_PAGE_STATE_JS)
        if isinstance(payload, dict):
            return payload
        return {"state": "unknown", "url": "", "readyState": "", "hasBusyOverlay": False, "hasSwal": False}

    async def _wait_page_ready(self, timeout: float = 2.8) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._stop_requested and asyncio.get_running_loop().time() < deadline:
            info = await self._read_state()
            ready = str(info.get("readyState") or "")
            busy = bool(info.get("hasBusyOverlay"))
            if ready in {"interactive", "complete"} and not busy:
                return
            await asyncio.sleep(0.04)

    async def _click_button(self, keywords: list[str], *, prefer_top: bool = False) -> bool:
        payload = await self.cdp.evaluate(READ_CLICKABLES_JS)
        buttons = list(payload or [])
        lowered = [keyword.lower() for keyword in keywords]
        candidates: list[tuple[tuple[int, int, float, float], dict[str, Any]]] = []
        for button in buttons:
            label = str(button.get("label") or "").strip().lower()
            if not label:
                continue
            if bool(button.get("disabled")):
                continue
            if str(button.get("pointerEvents") or "").strip().lower() == "none":
                continue
            matches = [keyword for keyword in lowered if keyword in label]
            if not matches:
                continue
            score = (
                1 if any(label == keyword for keyword in matches) else 0,
                max(len(keyword) for keyword in matches),
                -float(button.get("y") or 0) if prefer_top else float(button.get("y") or 0),
                float(button.get("x") or 0),
            )
            candidates.append((score, button))
        if not candidates:
            return False
        _, chosen = max(candidates, key=lambda item: item[0])
        await self._move_and_click(float(chosen["x"]), float(chosen["y"]))
        return True

    async def _read_showtimes(self) -> list[dict[str, Any]]:
        payload = await self.cdp.evaluate(READ_SHOWTIME_OPTIONS_JS)
        return list(payload or [])

    def _pick_showtime(self, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not options:
            return None
        preferred_id = str(self.event.presale_code or "").strip()
        preferred_text = str(self.config.vieshow.showtime_keyword or self.event.date_keyword or "").strip().lower()
        if preferred_id:
            for item in options:
                if str(item.get("optionId") or "").strip() == preferred_id:
                    return item
        if preferred_text:
            for item in options:
                text = str(item.get("text") or "").strip().lower()
                if preferred_text == text or preferred_text in text:
                    return item
        return options[0]

    async def _click_showtime(self) -> bool:
        options = await self._read_showtimes()
        target = self._pick_showtime(options)
        if target is None:
            self._report("ticket.aspx 找不到可點擊的場次時間。")
            return False
        await self._move_and_click(float(target["x"]), float(target["y"]))
        self._report(f"已點場次 {target.get('text') or ''}")
        return True

    async def _handle_dialog_if_any(self) -> bool:
        try:
            await self.cdp._send(  # noqa: SLF001
                "Page.handleJavaScriptDialog",
                {"accept": True},
            )
            self._report("已自動確認彈窗。")
            return True
        except CDPError:
            return False

    async def _ensure_sale_window(self) -> bool:
        sale_time = _parse_sale_time(self.event.sale_time)
        if sale_time is None:
            return True
        now = datetime.now()
        seconds_left = (sale_time - now).total_seconds()
        if seconds_left <= 0:
            return True
        bucket = int(seconds_left)
        self._report(f"距離開賣 {bucket}s，等待中。")
        await asyncio.sleep(min(0.7, max(0.05, seconds_left - 0.25)))
        return False

    async def _click_first_unchecked_agree(self) -> bool:
        payload = await self.cdp.evaluate(READ_CHECKBOXES_JS)
        checkboxes = list(payload or [])
        for item in checkboxes:
            label = str(item.get("label") or "")
            if bool(item.get("checked")):
                continue
            if any(word in label for word in ["同意", "規定", "條款", "agree"]):
                await self._move_and_click(float(item["x"]), float(item["y"]))
                return True
        return False

    async def _select_full_ticket_count(self) -> bool:
        # Expand "一般票種" first if collapsed.
        await self._click_button(["一般票種"], prefer_top=True)
        await asyncio.sleep(0.04)

        payload = await self.cdp.evaluate(READ_TICKET_SELECTS_JS)
        selects = list(payload or [])
        if not selects:
            self._report("票種頁找不到下拉選單。")
            return False

        exclude_words = ["優待", "學生", "愛心", "敬老", "會員", "銀行", "套票", "餐", "discount", "student"]
        target = None
        for item in selects:
            text_blob = " ".join(
                [
                    str(item.get("rowText") or ""),
                    str(item.get("label") or ""),
                    " ".join(str(h) for h in list(item.get("headers") or [])),
                ]
            ).lower()
            if "全票" not in text_blob and "full" not in text_blob and "一般" not in text_blob:
                continue
            if any(word.lower() in text_blob for word in exclude_words):
                continue
            target = item
            break

        if target is None:
            self._report("找不到一般票種中的全票下拉選單。")
            return False

        desired_count = max(1, int(self.event.ticket_count or 2))
        target_index = None
        for option in list(target.get("options") or []):
            option_text = str(option.get("text") or "")
            option_value = str(option.get("value") or "")
            parsed_text = _safe_int(option_text)
            parsed_value = _safe_int(option_value)
            if parsed_text == desired_count or parsed_value == desired_count:
                target_index = int(option.get("index") or 0)
                break

        if target_index is None:
            self._report(f"全票找不到可用數量 {desired_count}。")
            return False

        changed = await self.cdp.evaluate(
            f"""
            (() => {{
              const all = Array.from(document.querySelectorAll('select'));
              const domIndex = {int(target.get("domIndex") or -1)};
              if (domIndex < 0 || domIndex >= all.length) return false;
              const select = all[domIndex];
              select.selectedIndex = {target_index};
              select.dispatchEvent(new Event('input', {{ bubbles: true }}));
              select.dispatchEvent(new Event('change', {{ bubbles: true }}));
              return true;
            }})()
            """
        )
        if not changed:
            self._report("設定全票數量失敗。")
            return False
        self._report(f"已設定全票數量：{desired_count}")
        return True

    async def _run_state_machine(self) -> bool:
        while not self._stop_requested:
            await self._wait_page_ready(timeout=2.2)
            info = await self._read_state()
            state = str(info.get("state") or "unknown")
            if state != self._last_state:
                self._last_state = state
                self._report(f"目前狀態: {state}")

            if state == "ticket_showtime":
                if not await self._ensure_sale_window():
                    continue
                await self._click_showtime()
                await asyncio.sleep(0.03)
                await self._handle_dialog_if_any()
                await asyncio.sleep(0.05)
                continue

            if state == "booking_option":
                clicked = await self._click_button(
                    ["線上即時付款", "一般 / 銀行優惠", "general", "bank privilege"],
                    prefer_top=True,
                )
                if clicked:
                    self._report("booking.aspx 已點選上方方案。")
                await asyncio.sleep(0.05)
                continue

            if state == "booking_rules":
                await self._click_first_unchecked_agree()
                await self._click_button(["前往訂票", "立即訂票", "continue", "next"], prefer_top=False)
                await asyncio.sleep(0.05)
                continue

            if state == "ticket_type":
                ok = await self._select_full_ticket_count()
                if ok:
                    await self._click_button(["繼續", "下一步", "continue", "next"], prefer_top=False)
                await asyncio.sleep(0.05)
                continue

            if state == "seat_selection":
                await self._click_button(["繼續", "下一步", "continue", "next"], prefer_top=False)
                await asyncio.sleep(0.05)
                continue

            if state == "order_confirm":
                self.last_success_info = "Reached OrderConfirm"
                self._report("已到付款前確認頁（OrderConfirm）。")
                return True

            handled_dialog = await self._handle_dialog_if_any()
            await asyncio.sleep(0.03 if handled_dialog else 0.08)

        return False

    async def run(self) -> bool:
        takeover = self.config.vieshow.takeover
        cdp_url = takeover.resolved_cdp_url()
        page_filter = takeover.page_url_substring or "vscinemas.com.tw"
        self._report(f"Attach CDP: {cdp_url}")
        target = await self.cdp.connect(cdp_url=cdp_url, page_url_substring=page_filter)
        self._report(f"Attached tab: {target.url}")
        # Enable page domain so dialog handling is available.
        try:
            await self.cdp._send("Page.enable", {})  # noqa: SLF001
        except CDPError:
            pass
        return await self._run_state_machine()
