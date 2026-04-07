"""VieShow browser automation with raw-CDP takeover and legacy fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import random
import re
import socket
from typing import Any, Callable

from ticket_bot.browser import BrowserEngine, PageWrapper, create_engine
from ticket_bot.browser.cdp_takeover import CDPError, CDPTakeoverEngine
from ticket_bot.config import AppConfig, EventConfig, SessionConfig, TakeoverConfig
from ticket_bot.human import click_delay

logger = logging.getLogger(__name__)


BLOCKED_URL_PATTERNS = [
    "*google-analytics.com*",
    "*googletagmanager.com*",
    "*googlesyndication.com*",
    "*facebook.com/tr*",
    "*hotjar.com*",
    "*clarity.ms*",
    "*684d0d4a.akstat.io*",
    "*doubleclick.net*",
    "*adservice.google.com*",
]

VIESHOW_MAIN = "https://www.vscinemas.com.tw"
VIESHOW_TICKET_URL = f"{VIESHOW_MAIN}/vsTicketing/ticketing/ticket.aspx"
SALE_TIME_IN_MESSAGE_RE = re.compile(
    r"(?P<date>\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?)"
)

DETECT_STATE_JS = """
(() => {
    const url = location.href;
    const text = document.body ? document.body.innerText : '';
    const buttonText = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'))
        .map((node) => ((node && (node.innerText || node.value || node.textContent)) || '').replace(/\\s+/g, ' ').trim())
        .join(' ');

    if (document.querySelector('.swal2-popup, .swal2-container')) return 'error';

    if (url.includes('Member/Login') || url.includes('Member/Deposit')) return 'login_required';
    if (document.querySelector('input[type="password"]') &&
        document.querySelector('form[action*="login" i], form[action*="member" i]'))
        return 'login_required';

    if (url.includes('booking.aspx') ||
        document.querySelector('a[href="#bookNormal"], a[href="#bookGroup"], .icon-vsgeneral, .icon-vsgroup'))
        return 'booking_landing';

    if (url.includes('PaymentHistory') || url.includes('BookingRecord'))
        return 'order_history';

    if (url.includes('/Home/OrderConfirm') || url.includes('/Home/Payment'))
        return 'checkout';
    if (/請選擇付款方式|信用卡付款|儲值卡付款|orderconfirm/i.test(text))
        return 'checkout';
    if (document.querySelector('button, a, input[type="button"], input[type="submit"]') &&
        /信用卡付款|儲值卡付款|請輸入購買人資訊|請選擇付款方式/i.test(text))
        return 'checkout';

    if (url.includes('/Home/SelectSeats'))
        return 'seat_selection';
    if (/2\\.\\s*選擇座位|選擇座位|選位|seat/i.test(text) &&
        (document.querySelector('[class*="seat"]') || document.querySelector('.seat-map, #seatMap, [class*="seatmap"], [class*="seat-layout"]')))
        return 'seat_selection';
    if (document.querySelector('.seat-map, #seatMap, [class*="seatmap"], [class*="seat-layout"], svg [class*="seat"], canvas[id*="seat"]'))
        return 'seat_selection';
    if (document.querySelectorAll('[class*="seat"]').length > 5 &&
        /選擇座位|選位|seat/i.test(text))
        return 'seat_selection';

    if ((url.includes('LiveTicketT2') || url.includes('VieShowTicketT2')) &&
        document.querySelectorAll('select').length >= 3 &&
        /前往訂票|查看座位|快速訂票|快搜空位|線上訂票|book|seat/i.test(buttonText + ' ' + text) &&
        !/1\\.\\s*選擇票種|一般票種|全票|優待票/i.test(text))
        return 'quick_booking';

    if ((url.includes('VieShowTicketT2') || url.includes('agree=on')) &&
        (/1\\.\\s*選擇票種|選擇電影票|一般票種|全票|優待票/i.test(text) || document.querySelector('select')))
        return 'ticket_type';
    if (document.querySelector('select[name*="ticket" i], select[name*="qty" i], select[name*="quantity" i], select[id*="ticket" i], select[id*="qty" i], select[id*="quantity" i]'))
        return 'ticket_type';
    if ((/票種|ticket/i.test(text)) && document.querySelector('select'))
        return 'ticket_type';

    const theaterSelect = document.querySelector('#theater');
    const theaterSelected = !!(theaterSelect && theaterSelect.value && theaterSelect.value !== '');
    if (!theaterSelected && (document.querySelector('#theater') || document.querySelector('#show_movie_button')))
        return 'theater_selection';

    if (theaterSelected ||
        document.querySelectorAll('[class*="movie"], [class*="film"], [class*="showtime"]').length > 2 ||
        /場次|showtime|電影/i.test(text))
        return 'movie_list';

    return 'unknown';
})()
"""

READ_SEATS_JS = """
(() => {
    const isVisible = (node) => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const nodes = Array.from(document.querySelectorAll('[class*="seat"]')).filter(isVisible);
    return nodes.map((node, index) => {
        const rect = node.getBoundingClientRect();
        const cls = node.className && typeof node.className === 'string'
            ? node.className
            : (node.className && node.className.baseVal) || '';
        return {
            index,
            id: node.id || node.dataset.id || node.dataset.seat || '',
            row: node.dataset.row || node.getAttribute('data-row') || '',
            col: node.dataset.col || node.getAttribute('data-col') || node.dataset.seat || '',
            text: (node.textContent || '').replace(/\\s+/g, ' ').trim(),
            className: cls,
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
            width: rect.width,
            height: rect.height,
            disabled: !!(node.disabled || node.getAttribute('aria-disabled') === 'true')
        };
    });
})()
"""

READ_TICKET_CONTROLS_JS = """
(() => {
    const visible = (node) => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const center = (node) => {
        const rect = node.getBoundingClientRect();
        return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
    };
    const textOf = (node) => ((node && node.innerText) || (node && node.textContent) || '')
        .replace(/\\s+/g, ' ')
        .trim();

    const readHeaders = (node) => {
        const headers = [];
        let cursor = node.parentElement;
        while (cursor && headers.length < 5) {
            const heading = cursor.querySelector('button, a, .panel-title, .accordion-toggle, .card-header, h1, h2, h3, h4');
            const text = textOf(heading);
            if (text) headers.push(text);
            cursor = cursor.parentElement;
        }
        return Array.from(new Set(headers));
    };

    const selects = Array.from(document.querySelectorAll('select'))
        .map((node, index) => {
            const row = node.closest('tr');
            const scope = node.closest('tr, li, .ticket, .ticket-type, .ticketQty, .quantity, div, form') || node.parentElement || node;
            const point = center(node);
            const headers = readHeaders(node);
            return {
                index,
                label: textOf(scope),
                rowText: textOf(row),
                name: node.name || node.id || '',
                headers,
                visible: visible(node),
                x: point.x,
                y: point.y,
                selectedIndex: node.selectedIndex,
                options: Array.from(node.options).map((option, optionIndex) => ({
                    index: optionIndex,
                    text: textOf(option),
                    value: option.value || '',
                })),
            };
        });

    const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'))
        .filter(visible)
        .map((node, index) => {
            const label = textOf(node.closest('label, tr, li, div, form') || node.parentElement || node);
            const point = center(node);
            return {
                index,
                label,
                checked: !!node.checked,
                x: point.x,
                y: point.y,
            };
        });

    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], a[href], [role="button"]'))
        .filter(visible)
        .map((node, index) => {
            const point = center(node);
            const cls = node.className && typeof node.className === 'string'
                ? node.className
                : (node.className && node.className.baseVal) || '';
            const style = window.getComputedStyle(node);
            const disabled = !!(
                node.disabled ||
                node.getAttribute('aria-disabled') === 'true' ||
                /disabled|is-disabled/.test(cls)
            );
            return {
                index,
                label: textOf(node) || node.value || '',
                disabled,
                className: cls,
                pointerEvents: style ? (style.pointerEvents || '') : '',
                x: point.x,
                y: point.y,
            };
        });

    return {
        selects,
        checkboxes,
        buttons,
        viewportHeight: window.innerHeight || document.documentElement.clientHeight || 0,
        viewportWidth: window.innerWidth || document.documentElement.clientWidth || 0,
    };
})()
"""

READ_ERROR_JS = """
(() => {
    const title = document.querySelector('.swal2-title');
    const body = document.querySelector('.swal2-html-container, .swal2-content');
    const button = document.querySelector('.swal2-confirm, .swal2-actions button, .swal2-actions a');
    if (!title && !body) return null;
    const rect = button ? button.getBoundingClientRect() : null;
    return {
        message: [title?.textContent || '', body?.textContent || ''].join(' ').replace(/\\s+/g, ' ').trim(),
        button: rect ? {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2} : null,
    };
})()
"""

READ_CHECKOUT_JS = """
(() => {
    const text = document.body ? document.body.innerText : '';
    const totalMatch = text.match(/(?:總計|Total|合計)[\\s:：]*(?:NT\\$?|TWD)?\\s*([\\d,]+)/i);
    return {
        url: location.href,
        title: document.title,
        total: totalMatch ? totalMatch[1] : '',
    };
})()
"""

READ_PAGE_ACTIVITY_JS = """
(() => {
    const isVisible = (node) => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(node);
        if (!style) return false;
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (Number(style.opacity || '1') < 0.05) return false;
        return true;
    };

    const selectors = [
        '.loading',
        '.loader',
        '.spinner',
        '.busy',
        '.blockUI',
        '.pace-active',
        '.swal2-loading',
        '.swal2-loader',
        '[aria-busy="true"]',
        '[data-loading="true"]',
    ];
    const hasBusyOverlay = selectors.some((selector) => {
        const node = document.querySelector(selector);
        return isVisible(node);
    });

    const bodyText = ((document.body && document.body.innerText) || '').toLowerCase();
    const textBusy = /(loading|please\\s+wait|載入中|請稍候|讀取中|處理中)/i.test(bodyText);
    const readyState = document.readyState || 'loading';
    const domNodes = document.querySelectorAll('body *').length;
    const url = location.href || '';
    const lowDomBusy = domNodes < 20 && /(vieshow|vscinemas|ticket)/i.test(url);

    return {
        readyState,
        hasBusyOverlay,
        textBusy,
        domNodes,
        busy: readyState === 'loading' || hasBusyOverlay || textBusy || lowDomBusy,
    };
})()
"""

READ_TAKEOVER_FLOW_HINTS_JS = """
(() => {
    const url = location.href || '';
    const text = (document.body && document.body.innerText) || '';
    const buttonText = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'))
        .map((node) => ((node && (node.innerText || node.value || node.textContent)) || '').replace(/\\s+/g, ' ').trim())
        .join(' ');
    const selectNodes = Array.from(document.querySelectorAll('select'));
    const ticketSelect = document.querySelector(
        'select[name*="ticket" i], select[name*="qty" i], select[name*="quantity" i], ' +
        'select[id*="ticket" i], select[id*="qty" i], select[id*="quantity" i]'
    );
    return {
        url,
        liveTicketLike: /LiveTicketT2|VieShowTicketT2|agree=on/i.test(url),
        selectCount: selectNodes.length,
        ticketSelectCount: ticketSelect ? 1 : 0,
        hasBookButton: /\\u524d\\u5f80\\u8a02\\u7968|\\u7acb\\u5373\\u8a02\\u7968|\\u67e5\\u770b\\u5ea7\\u4f4d|book|continue|next/i.test(buttonText),
        hasTicketMarker: /\\u9078\\u64c7\\u7968\\u7a2e|\\u4e00\\u822c\\u7968\\u7a2e|\\u5168\\u7968|\\u512a\\u5f85\\u7968|\\u7968\\u7a2e|ticket/i.test(text),
        hasPresaleMarker: /\\u5c1a\\u672a\\u958b\\u8ce3|\\u5373\\u5c07\\u958b\\u8ce3|\\u958b\\u8ce3\\u6642\\u9593|coming\\s*soon/i.test(text),
    };
})()
"""

READ_SHOWTIME_BUTTONS_JS = """
(() => {
    const isVisible = (node) => {
        if (!node) return false;
        const rect = node.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        const style = window.getComputedStyle(node);
        if (!style) return false;
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (Number(style.opacity || '1') < 0.05) return false;
        return true;
    };

    const textOf = (node) => ((node && (node.innerText || node.textContent || node.value)) || '')
        .replace(/\\s+/g, ' ')
        .trim();

    const center = (node) => {
        const rect = node.getBoundingClientRect();
        return {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
            top: rect.top,
            left: rect.left,
        };
    };

    const selectors = [
        'a',
        'button',
        'input[type="button"]',
        'input[type="submit"]',
        '[onclick]',
        '[role="button"]',
    ];

    const seen = new Set();
    const candidates = [];
    for (const node of document.querySelectorAll(selectors.join(','))) {
        if (!isVisible(node)) continue;
        if (node.disabled || node.getAttribute('aria-disabled') === 'true') continue;
        const text = textOf(node);
        if (!/^\\d{1,2}:\\d{2}$/.test(text)) continue;
        const point = center(node);
        const key = `${Math.round(point.left)}:${Math.round(point.top)}:${text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        candidates.push({
            text,
            x: point.x,
            y: point.y,
            top: point.top,
            left: point.left,
        });
    }

    candidates.sort((a, b) => (a.top - b.top) || (a.left - b.left));
    return candidates;
})()
"""

LEGACY_SELECT_SEATS_JS = """
((seatIds, indexes) => {
    const nodes = Array.from(document.querySelectorAll('[class*="seat"]')).filter(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    });

    const wanted = new Set(seatIds || []);
    const picked = [];
    nodes.forEach((node, index) => {
        const id = node.id || node.dataset.id || node.dataset.seat || '';
        if ((wanted.size > 0 && wanted.has(id)) || (wanted.size === 0 && indexes.includes(index))) {
            node.click();
            picked.push(id || `seat-${index}`);
        }
    });
    return picked;
})
"""

LEGACY_SELECT_TICKET_JS = """
((keywords, count) => {
    const normalized = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    let matched = 0;
    for (const sel of document.querySelectorAll('select')) {
        const scope = normalized((sel.closest('tr, li, div, form') || sel.parentElement || sel).innerText || '');
        const isTargetType = keywords.some((keyword) => scope.includes(normalized(keyword)));
        if (isTargetType) {
            const target = Array.from(sel.options).find(option => {
                const text = normalized(option.textContent || '');
                return text === String(count) || text.includes(String(count)) || option.value === String(count);
            });
            if (target) {
                sel.value = target.value;
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                matched += 1;
            }
        }
    }

    for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
        const text = normalized((cb.closest('label, tr, li, div, form') || cb.parentElement || cb).innerText || '');
        if (!cb.checked && /(同意|agree|條款|規定)/i.test(text)) {
            cb.checked = true;
            cb.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }

    return {matched};
})
"""


def _ticket_type_keywords(ticket_type: str) -> list[str]:
    mapping = {
        "full": ["全票", "一般", "成人", "adult", "standard"],
        "student": ["優待", "學生", "兒童", "student", "child"],
        "ishow": ["ishow", "會員", "儲值", "member"],
        "senior": ["敬老", "senior"],
        "love": ["愛心", "disabled"],
    }
    return mapping.get(ticket_type.lower(), [ticket_type])


def _ticket_row_keywords(ticket_type: str) -> list[str]:
    mapping = {
        "full": ["全票"],
        "student": ["優待票", "學生票", "兒童票", "軍警票", "優待"],
        "ishow": ["會員票", "儲值金會員票", "儲值金會員", "ishow"],
        "senior": ["敬老票", "敬老"],
        "love": ["愛心票", "愛心"],
    }
    normalized = ticket_type.lower()
    keywords = list(mapping.get(normalized, [ticket_type]))
    english_aliases = {
        "full": ["full", "adult", "normal", "general", "standard"],
        "student": ["student", "discount", "child"],
        "ishow": ["ishow", "member"],
        "senior": ["senior", "elder"],
        "love": ["love", "disabled"],
    }
    keywords.extend(english_aliases.get(normalized, []))
    # Keep order but deduplicate to stabilize matching.
    return list(dict.fromkeys(keyword for keyword in keywords if keyword))


def _ticket_row_exclude_keywords(ticket_type: str) -> list[str]:
    mapping = {
        "full": ["優待", "學生", "孩童", "軍警", "敬老", "愛心", "會員", "套票", "銀行", "飲料", "爆米花"],
        "student": ["全票", "敬老", "愛心", "會員", "套票", "銀行"],
        "ishow": ["全票", "優待", "學生", "敬老", "愛心", "套票", "銀行"],
        "senior": ["全票", "優待", "學生", "愛心", "會員", "套票", "銀行"],
        "love": ["全票", "優待", "學生", "敬老", "會員", "套票", "銀行"],
    }
    normalized = ticket_type.lower()
    excludes = list(mapping.get(normalized, []))
    english_excludes = {
        "full": ["student", "discount", "child", "senior", "elder", "love", "member", "bundle", "combo"],
        "student": ["full", "adult", "normal", "senior", "member", "bundle", "combo"],
        "ishow": ["full", "adult", "normal", "student", "discount", "bundle", "combo"],
        "senior": ["full", "adult", "normal", "student", "discount", "member", "bundle", "combo"],
        "love": ["full", "adult", "normal", "student", "discount", "member", "bundle", "combo"],
    }
    excludes.extend(english_excludes.get(normalized, []))
    return list(dict.fromkeys(keyword for keyword in excludes if keyword))


def _ticket_section_for_type(ticket_type: str) -> str:
    mapping = {
        "full": "一般票種",
        "student": "一般票種",
        "senior": "一般票種",
        "love": "一般票種",
        "ishow": "會員票種",
        "member": "會員票種",
        "bundle": "優惠套票",
        "package": "優惠套票",
    }
    return mapping.get(ticket_type.lower(), "一般票種")


class VieShowBot:
    """VieShow automation with takeover-first architecture."""

    def __init__(
        self,
        config: AppConfig,
        event: EventConfig,
        session: SessionConfig | None = None,
    ):
        self.config = config
        self.event = event
        self.session = session
        self.engine: BrowserEngine = create_engine(config.browser.engine)
        self.page: PageWrapper | None = None
        self.cdp: CDPTakeoverEngine | None = None
        self.last_success_info: str = ""
        self._session_label = session.name if session else "default"
        self._status_callback: Callable[[str], None] | None = None
        self._stop_requested = False
        self._last_mouse_pos = (160.0, 140.0)
        self._last_reported_state = ""
        self._sale_time_cache_raw = ""
        self._sale_time_cache: datetime | None = None
        self._sale_countdown_reported = -1
        self._sale_burst_reported = False
        self._sale_time_missing_reported = False
        self._pre_sale_probe_reported = False

    def set_status_callback(self, callback: Callable[[str], None]) -> None:
        self._status_callback = callback

    def request_stop(self) -> None:
        self._stop_requested = True

    def _report(self, msg: str) -> None:
        logger.info("[%s] %s", self._session_label, msg)
        if self._status_callback:
            try:
                self._status_callback(msg)
            except Exception:
                logger.debug("status callback failed", exc_info=True)

    def _takeover_config(self) -> TakeoverConfig:
        cfg = self.config.vieshow.takeover
        if not cfg.enabled and (
            getattr(self.config.vieshow, "takeover_mode", False)
            or getattr(self.config.browser, "takeover_from_current_page", False)
        ):
            cfg.enabled = True
        if not cfg.cdp_url:
            cfg.cdp_url = (
                self.config.vieshow.attach_cdp_url
                or self.config.browser.attach_cdp_url
                or ""
            )
        if not cfg.page_url_substring:
            cfg.page_url_substring = (
                self.config.vieshow.attach_page_url_substring
                or self.config.browser.attach_page_url_substring
                or "vscinemas.com.tw"
            )
        return cfg

    def _is_takeover_mode(self) -> bool:
        return bool(self._takeover_config().enabled)

    def _is_takeover_turbo(self) -> bool:
        return bool(self.config.browser.turbo_mode)

    def _takeover_poll(self, normal: float, *, turbo: float = 0.01) -> float:
        if self._is_takeover_turbo():
            return min(normal, turbo)
        return normal

    async def _move_mouse_takeover(
        self,
        target: tuple[float, float],
        *,
        duration_ms_range: tuple[int, int] = (70, 150),
    ) -> None:
        assert self.cdp is not None
        x, y = target
        if self._is_takeover_turbo():
            if hasattr(self.cdp, "dispatch_mouse_event"):
                await self.cdp.dispatch_mouse_event("mouseMoved", x, y, button="none", click_count=0)
            self._last_mouse_pos = (x, y)
            return

        await self.cdp.human_mouse_move(
            self._last_mouse_pos,
            (x, y),
            duration_ms=random.randint(*duration_ms_range),
        )
        self._last_mouse_pos = (x, y)

    async def _click_point_takeover(self, x: float, y: float) -> None:
        assert self.cdp is not None
        if self._is_takeover_turbo():
            if hasattr(self.cdp, "dispatch_mouse_event"):
                await self.cdp.dispatch_mouse_event("mouseMoved", x, y, button="none", click_count=0)
                await self.cdp.dispatch_mouse_event("mousePressed", x, y, click_count=1)
                await self.cdp.dispatch_mouse_event("mouseReleased", x, y, click_count=1)
            else:
                await self.cdp.dispatch_click(x, y)
            self._last_mouse_pos = (x, y)
            return

        await asyncio.sleep(random.uniform(0.01, 0.04))
        await self.cdp.dispatch_click(x, y)
        self._last_mouse_pos = (x, y)

    async def start_browser(self) -> None:
        if self._is_takeover_mode():
            if self.cdp is None:
                cfg = self._takeover_config()
                self.cdp = CDPTakeoverEngine()
                target = await self.cdp.connect(
                    cfg.resolved_cdp_url(),
                    page_url_substring=cfg.page_url_substring,
                )
                self._report(f"已附著到 Chrome 分頁: {target.url or target.title}")
            return

        user_data_dir = self.session.user_data_dir if self.session else self.config.browser.user_data_dir
        proxy_server = self.session.proxy_server if self.session and self.session.proxy_server else ""
        await self.engine.launch(
            headless=self.config.browser.headless,
            user_data_dir=user_data_dir,
            executable_path=self.config.browser.executable_path,
            lang=self.config.browser.lang,
            proxy_server=proxy_server,
        )
        self._report(f"瀏覽器啟動完成 (engine={self.config.browser.engine})")

    async def pre_warm(self) -> None:
        if self._is_takeover_mode():
            return

        dns_task = asyncio.get_event_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo("www.vscinemas.com.tw", 443),
        )
        await self._open_page(self.event.url or VIESHOW_TICKET_URL)
        try:
            await dns_task
        except Exception:
            logger.debug("DNS pre-warm failed", exc_info=True)
        self._report("預熱完成")

    async def _open_page(self, url: str) -> None:
        if self.page is None:
            self.page = await self.engine.new_page(url)
            await self.page.block_urls(BLOCKED_URL_PATTERNS)
        else:
            await self.page.goto(url)

    async def close(self) -> None:
        if self.cdp is not None:
            await self.cdp.close()
            self.cdp = None
        try:
            await self.engine.close()
        except Exception:
            logger.debug("legacy engine close failed", exc_info=True)
        self.page = None

    async def run(self) -> bool:
        if self._is_takeover_mode():
            return await self.run_takeover()
        return await self.run_legacy()

    async def watch(self, interval: float = 5.0) -> bool:
        if self._is_takeover_mode():
            self._report("takeover 模式不使用舊的 watch 迴圈，改為直接接手。")
            return await self.run_takeover()

        if self.page is None:
            await self.start_browser()
            await self.pre_warm()

        self._report(f"開始釋票監控（每 {interval} 秒刷新一次）")
        round_count = 0
        while not self._stop_requested:
            round_count += 1
            try:
                state = await self._detect_legacy_state()
                if state in {"seat_selection", "ticket_type", "booking_landing", "checkout"}:
                    self._report("偵測到可處理的訂票狀態，切入 legacy 流程")
                    return await self.run_legacy()
                if state == "error":
                    await self._handle_error_legacy()
                elif self.page is not None:
                    await self.page.evaluate("window.location.reload()")
                    await self.page.sleep(interval)
                if round_count % 5 == 0:
                    self._report(f"監看中... 第 {round_count} 輪")
            except Exception:
                logger.exception("VieShow watch loop failed")
                if self.page is not None:
                    await self.page.sleep(interval)
        return False

    async def run_takeover(self) -> bool:
        await self.start_browser()
        assert self.cdp is not None

        self._report("接管模式啟動：請先在 Chrome 中手動完成登入、Cloudflare、場次選擇，並停在 booking.aspx 規定頁或後續頁面")
        booking_landing_done = False
        ticket_done = False
        seats_done = False
        seat_attempts = 0

        while not self._stop_requested:
            try:
                state = await self._detect_takeover_state()
            except CDPError as exc:
                self._report(f"CDP 指令失敗: {exc}")
                await asyncio.sleep(0.12 if self._is_takeover_turbo() else 0.35)
                continue

            if state != self._last_reported_state:
                self._report(f"目前狀態: {state}")
                self._last_reported_state = state
            if state != "seat_selection":
                seat_attempts = 0

            seconds_left = self._seconds_until_sale_takeover()
            if (
                seconds_left is not None
                and seconds_left > 0
                and not ticket_done
                and not seats_done
                and state in {"unknown", "pre_sale_wait", "quick_booking", "movie_list", "booking_landing", "ticket_type"}
            ):
                await self._sleep_until_sale_window_takeover(seconds_left)
                continue

            try:
                activity = await self._read_page_activity_takeover()
            except CDPError:
                activity = {"busy": True}
            busy = bool(activity.get("busy"))
            ready_state = str(activity.get("readyState") or "")
            overlay_busy = bool(activity.get("hasBusyOverlay"))
            if state in {"booking_landing", "ticket_type", "seat_selection"} and busy:
                await self._wait_for_page_ready_takeover(timeout=2.6, poll=0.05)
                continue
            if state in {"unknown", "pre_sale_wait", "quick_booking", "movie_list"} and (ready_state == "loading" or overlay_busy):
                await self._wait_for_page_ready_takeover(timeout=2.2, poll=0.05)
                continue

            if state == "pre_sale_wait" and not ticket_done and not seats_done:
                if seconds_left is None:
                    clicked_probe = await self._click_default_showtime_takeover()
                    if clicked_probe and not self._pre_sale_probe_reported:
                        self._pre_sale_probe_reported = True
                        self._report("尚未開賣，已先點選最上方場次並等待開賣時間。")
                    if not self._sale_time_missing_reported:
                        self._sale_time_missing_reported = True
                        self._report("目前是 pre_sale_wait，請先設定開賣時間（Sale time）後再開始接手。")
                    await asyncio.sleep(0.18 if self._is_takeover_turbo() else 0.5)
                    continue
                entered = await self._rush_quick_booking_takeover()
                if entered:
                    self._report("已從快速訂票頁進入後續流程")
                continue

            if state in {"quick_booking", "movie_list"} and not ticket_done and not seats_done:
                entered = await self._rush_quick_booking_takeover()
                if entered:
                    self._report("撌脣?敹恍?蟡券??脣敺?瘚?")
                continue

            if state == "booking_landing" and not booking_landing_done:
                booking_landing_done = await self._enter_booking_flow_takeover()
                if booking_landing_done:
                    await self._wait_for_state_change_takeover("booking_landing", timeout=3.0, poll=0.05)
                continue

            if state == "ticket_type" and not ticket_done:
                if not booking_landing_done:
                    booking_landing_done = True
                    self._report("偵測到已從規定頁進入票種頁，開始選擇全票數量")
                ticket_done = await self._select_ticket_type_takeover()
                if ticket_done:
                    await self._wait_for_state_change_takeover("ticket_type", timeout=3.0, poll=0.05)
                continue

            if state == "ticket_type" and ticket_done and not seats_done:
                advanced = await self._advance_ticket_type_takeover(attempts=2)
                if advanced:
                    self._report("票種頁已前進到下一步")
                continue

            if state == "seat_selection" and not seats_done:
                seat_attempts += 1
                if not booking_landing_done:
                    booking_landing_done = True
                    self._report("偵測到已跳過規定頁，直接從座位圖接手")
                if not ticket_done:
                    ticket_done = True
                    self._report("偵測到已進入座位圖，視為票種數量已完成")
                picked = await self._grab_seats_takeover()
                seats_done = False
                if picked:
                    next_state = await self._wait_for_state_change_takeover("seat_selection", timeout=3.0, poll=0.05)
                    seats_done = next_state != "seat_selection"
                    if not seats_done:
                        await self._wait_for_page_ready_takeover(timeout=2.0, poll=0.05)
                        latest_state = await self._detect_takeover_state()
                        seats_done = latest_state != "seat_selection"
                if not seats_done and seat_attempts >= 4:
                    self._report("座位頁嘗試多次仍無法自動前進，請手動按繼續進入付款頁後再重新接手。")
                    return False
                continue

            if state == "checkout":
                if not booking_landing_done:
                    booking_landing_done = True
                if not ticket_done:
                    ticket_done = True
                if not seats_done:
                    seats_done = True
                await self._handle_checkout_takeover()
                return True

            if state == "error":
                await self._handle_error_takeover()
            elif state == "order_history":
                self._report("目前頁面落在訂票記錄 / PaymentHistory，這不是正確流程頁面。請回到 booking.aspx 規定頁後再開始接手。")
            elif state == "login_required":
                self._report("請先在 Chrome 中完成登入，再讓 bot 繼續接手。")

            if self._is_takeover_turbo():
                await asyncio.sleep(0.015 + random.uniform(0.0, 0.01))
            else:
                await asyncio.sleep(0.08 + random.uniform(0.0, 0.05))

        return False

    async def run_legacy(self) -> bool:
        if self.page is None:
            await self.start_browser()
            if self.config.browser.pre_warm:
                await self.pre_warm()
            else:
                await self._open_page(self.event.url or VIESHOW_TICKET_URL)

        self._report("legacy VieShow 流程啟動")
        consecutive_errors = 0
        while not self._stop_requested:
            try:
                state = await self._detect_legacy_state()
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                logger.exception("detect legacy state failed")
                if self.page is not None:
                    await self.page.sleep(0.5)
                if consecutive_errors > 5:
                    return False
                continue

            if state == "theater_selection":
                await self._select_theater_legacy()
            elif state == "movie_list":
                await self._select_movie_showtime_legacy()
            elif state == "booking_landing":
                await self._enter_booking_flow_legacy()
            elif state == "seat_selection":
                await self._select_seats_legacy()
            elif state == "ticket_type":
                await self._select_ticket_type_legacy()
            elif state == "checkout":
                await self._handle_checkout_legacy()
                return True
            elif state == "login_required":
                await self._login_ishow_legacy()
            elif state == "error":
                await self._handle_error_legacy()
            else:
                if self.page is not None and not await self.page.handle_cloudflare():
                    self._report("未知頁面狀態，等待中...")
                    await self.page.sleep(1.0)
                else:
                    await asyncio.sleep(0.4)

        return False

    async def _detect_takeover_state(self) -> str:
        assert self.cdp is not None
        state = str((await self.cdp.evaluate(DETECT_STATE_JS)) or "unknown")
        if state != "ticket_type":
            return state

        # Guard: some pre-sale LiveTicket pages contain "1.選擇票種" text but no real ticket controls.
        try:
            hint_raw = await self.cdp.evaluate(READ_TAKEOVER_FLOW_HINTS_JS)
        except CDPError:
            return state

        hints = dict(hint_raw or {})
        return self._normalize_takeover_state(state, hints)

    async def _detect_legacy_state(self) -> str:
        assert self.page is not None
        state = await self.page.evaluate(DETECT_STATE_JS)
        return str(state or "unknown")

    def _normalize_takeover_state(self, state: str, hints: dict[str, Any]) -> str:
        if state != "ticket_type":
            return state

        if not bool(hints.get("liveTicketLike")):
            return state

        ticket_select_count = int(hints.get("ticketSelectCount") or 0)
        select_count = int(hints.get("selectCount") or 0)
        has_book_button = bool(hints.get("hasBookButton"))
        has_presale_marker = bool(hints.get("hasPresaleMarker"))

        if ticket_select_count > 0:
            return state

        # LiveTicket pre-sale/not-open window should not enter ticket_type flow.
        if has_book_button or select_count >= 3:
            return "quick_booking"
        if has_presale_marker or select_count <= 1:
            return "pre_sale_wait"
        return "movie_list"

    def _now_local(self) -> datetime:
        return datetime.now().astimezone()

    def _parse_sale_time_takeover(self) -> datetime | None:
        raw = str(self.event.sale_time or "").strip()
        if not raw:
            return None
        if raw == self._sale_time_cache_raw:
            return self._sale_time_cache

        normalized = (
            raw.replace("T", " ")
            .replace("：", ":")
            .replace("年", "/")
            .replace("月", "/")
            .replace("日", " ")
        )
        normalized = (
            normalized.replace("：", ":")
            .replace("／", "/")
            .replace("年", "/")
            .replace("月", "/")
            .replace("日", " ")
        )
        normalized = (
            normalized.replace("：", ":")
            .replace("／", "/")
            .replace("年", "/")
            .replace("月", "/")
            .replace("日", " ")
        )
        normalized = " ".join(normalized.split())
        parsed: datetime | None = None
        now = self._now_local()
        timezone = now.tzinfo

        formats = [
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d %H:%M:%S",
            "%Y%m%d %H:%M",
            "%H:%M:%S",
            "%H:%M",
        ]
        for fmt in formats:
            try:
                candidate = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            if fmt in {"%H:%M:%S", "%H:%M"}:
                parsed = candidate.replace(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                    tzinfo=timezone,
                )
            else:
                parsed = candidate.replace(tzinfo=timezone)
            break

        self._sale_time_cache_raw = raw
        self._sale_time_cache = parsed
        return parsed

    def _seconds_until_sale_takeover(self) -> float | None:
        sale_time = self._parse_sale_time_takeover()
        if sale_time is None:
            return None
        return (sale_time - self._now_local()).total_seconds()

    async def _sleep_until_sale_window_takeover(self, seconds_left: float) -> None:
        if seconds_left <= 0:
            return
        bucket = max(0, int(seconds_left))
        if bucket != self._sale_countdown_reported:
            self._sale_countdown_reported = bucket
            self._report(f"尚未開賣，倒數 {bucket}s")
        max_wait = 0.35 if self._is_takeover_turbo() else 0.7
        min_wait = 0.06 if self._is_takeover_turbo() else 0.18
        sleep_for = min(max_wait, max(min_wait, seconds_left - 0.6))
        await asyncio.sleep(sleep_for)

    def _update_sale_time_from_message_takeover(self, message: str) -> str | None:
        normalized = " ".join(str(message or "").split())
        if not normalized:
            return None
        lowered = normalized.lower()
        has_presale_hint = any(
            keyword in normalized
            for keyword in ["開放訂購", "開放購票", "尚未開賣", "即將開賣", "開賣"]
        ) or any(keyword in lowered for keyword in ["coming soon", "open for booking"])
        if not has_presale_hint:
            return None

        match = SALE_TIME_IN_MESSAGE_RE.search(normalized)
        if not match:
            return None
        date_part = match.group("date").replace("-", "/")
        time_part = match.group("time")
        if len(time_part) == 5:
            time_part = f"{time_part}:00"
        sale_time_text = f"{date_part} {time_part}"

        if str(self.event.sale_time or "").strip() != sale_time_text:
            self.event.sale_time = sale_time_text
            self._sale_time_cache_raw = ""
            self._sale_time_cache = None
            self._sale_countdown_reported = -1
            self._sale_burst_reported = False
            self._sale_time_missing_reported = False
            self._report(f"偵測到開賣時間：{sale_time_text}，將等待開賣後再衝刺。")
        return sale_time_text

    async def _read_showtime_buttons_takeover(self) -> list[dict[str, Any]]:
        assert self.cdp is not None
        info = await self.cdp.evaluate(READ_SHOWTIME_BUTTONS_JS)
        return list(info or [])

    async def _click_default_showtime_takeover(self) -> bool:
        buttons = await self._read_showtime_buttons_takeover()
        if not buttons:
            return False

        preferred_keyword = str(self.config.vieshow.showtime_keyword or "").strip().lower()
        target = None
        if preferred_keyword:
            for item in buttons:
                text = str(item.get("text") or "").lower()
                if preferred_keyword in text:
                    target = item
                    break
        if target is None:
            target = buttons[0]

        x = float(target.get("x") or 0)
        y = float(target.get("y") or 0)
        if x <= 0 or y <= 0:
            return False
        await self._move_mouse_takeover((x, y), duration_ms_range=(28, 70))
        await self._click_point_takeover(x, y)
        return True

    async def _click_primary_booking_option_takeover(self) -> bool:
        controls = await self._read_ticket_controls_takeover()
        labels = " ".join(str(button.get("label") or "").lower() for button in list(controls.get("buttons") or []))
        option_markers = [
            "線上即時付款",
            "一般 / 銀行優惠",
            "會員票種",
            "general",
            "bank privilege",
            "svc discount",
            "corporate movie money",
        ]
        if not any(marker.lower() in labels for marker in option_markers):
            return False

        clicked = await self._click_best_button_takeover(
            [
                "線上即時付款",
                "一般 / 銀行優惠",
                "會員票種",
                "general",
                "bank privilege",
                "svc discount",
            ]
        )
        clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        if not clicked:
            await self._wait_for_page_ready_takeover(timeout=1.8, poll=0.05)
            clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        clicked = await self._click_best_button_takeover(
            ["線上即時付款", "一般 / 銀行優惠", "會員票種", "general", "bank privilege", "svc discount"]
        )
        clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        if not clicked:
            await self._wait_for_page_ready_takeover(timeout=1.8, poll=0.05)
            clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        if clicked:
            self._report("已在方案頁預設點選上方選項（線上即時付款）。")
        return clicked

    async def _rush_quick_booking_takeover(self) -> bool:
        sale_time = self._parse_sale_time_takeover()
        now = self._now_local()
        burst_click_loops = 20 if self._is_takeover_turbo() else 10
        inter_click_sleep = 0.008 if self._is_takeover_turbo() else 0.04
        post_click_sleep = 0.004 if self._is_takeover_turbo() else 0.02
        if sale_time is not None:
            seconds_left = (sale_time - now).total_seconds()
            if seconds_left > 1.2:
                bucket = int(seconds_left)
                if bucket != self._sale_countdown_reported:
                    self._sale_countdown_reported = bucket
                    self._report(f"未開賣待命中，距離開賣約 {bucket}s")
                max_wait = 0.35 if self._is_takeover_turbo() else 0.7
                min_wait = 0.08 if self._is_takeover_turbo() else 0.2
                await asyncio.sleep(min(max_wait, max(min_wait, seconds_left - 1.0)))
                return False
            if seconds_left > 0:
                await asyncio.sleep(max(0.01, seconds_left - 0.02))

        if not self._sale_burst_reported:
            self._sale_burst_reported = True
            self._report("進入開賣窗口，開始高速點擊前往訂票")

        for _ in range(burst_click_loops):
            clicked_showtime = await self._click_default_showtime_takeover()
            if clicked_showtime:
                await asyncio.sleep(post_click_sleep)
                state_after_showtime = await self._detect_takeover_state()
                if state_after_showtime in {"booking_landing", "ticket_type", "seat_selection", "checkout"}:
                    return True
                if state_after_showtime == "error":
                    await self._handle_error_takeover()
                    seconds_after_error = self._seconds_until_sale_takeover()
                    if seconds_after_error is not None and seconds_after_error > 0:
                        return False
                    await asyncio.sleep(inter_click_sleep)
                    continue
            clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "查看座位", "查詢座位", "繼續", "下一步", "continue", "next"])
            clicked = await self._click_best_button_takeover(
                ["前往訂票", "立即訂票", "查看座位", "查詢座位", "繼續", "下一步", "continue", "next"]
            )
            if clicked:
                await asyncio.sleep(post_click_sleep)

            state = await self._detect_takeover_state()
            if state in {"booking_landing", "ticket_type", "seat_selection", "checkout"}:
                return True
            if state == "error":
                await self._handle_error_takeover()
                seconds_after_error = self._seconds_until_sale_takeover()
                if seconds_after_error is not None and seconds_after_error > 0:
                    return False
            await asyncio.sleep(inter_click_sleep)

        return False

    def _filter_available_seats(self, seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        available = []
        for seat in seats:
            text = f"{seat.get('className', '')} {seat.get('text', '')}".lower()
            if seat.get("disabled"):
                continue
            if any(flag in text for flag in ["occupied", "taken", "sold", "disabled", "unavailable", "reserved"]):
                continue
            if seat.get("width", 0) <= 0 or seat.get("height", 0) <= 0:
                continue
            available.append(seat)
        return available

    def _pick_best_seats(self, seats: list[dict[str, Any]], count: int, preference: str) -> list[dict[str, Any]]:
        if not seats or count <= 0:
            return []

        requested = [part.strip().upper() for part in preference.split(",") if part.strip()]
        if len(requested) > 1:
            lookup = {}
            for seat in seats:
                key = (seat.get("id") or seat.get("text") or "").upper()
                if key:
                    lookup[key] = seat
            picked = [lookup[item] for item in requested if item in lookup]
            return picked[:count]

        rows: dict[str, list[dict[str, Any]]] = {}
        for seat in seats:
            row = str(seat.get("row") or f"y:{round(float(seat.get('y', 0)) / 12)}")
            rows.setdefault(row, []).append(seat)

        for row_seats in rows.values():
            row_seats.sort(key=lambda seat: float(seat.get("x", 0)))

        center_y = sum(float(seat.get("y", 0)) for seat in seats) / len(seats)
        center_x = sum(float(seat.get("x", 0)) for seat in seats) / len(seats)

        def score(group: list[dict[str, Any]]) -> tuple[float, float]:
            avg_x = sum(float(seat.get("x", 0)) for seat in group) / len(group)
            avg_y = sum(float(seat.get("y", 0)) for seat in group) / len(group)
            if preference == "front":
                return (avg_y, abs(avg_x - center_x))
            if preference == "back":
                return (-avg_y, abs(avg_x - center_x))
            return (abs(avg_x - center_x) + abs(avg_y - center_y), abs(avg_y - center_y))

        candidates: list[list[dict[str, Any]]] = []
        for row in rows.values():
            if len(row) < count:
                continue
            for start in range(0, len(row) - count + 1):
                candidates.append(row[start : start + count])

        if candidates:
            return min(candidates, key=score)

        fallback = sorted(
            seats,
            key=lambda seat: (
                abs(float(seat.get("x", 0)) - center_x),
                abs(float(seat.get("y", 0)) - center_y),
            ),
        )
        return fallback[:count]

    async def _read_page_activity_takeover(self) -> dict[str, Any]:
        assert self.cdp is not None
        info = await self.cdp.evaluate(READ_PAGE_ACTIVITY_JS)
        return dict(info or {})

    async def _wait_for_page_ready_takeover(self, timeout: float = 5.0, poll: float = 0.06) -> bool:
        poll = self._takeover_poll(poll, turbo=0.01)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, poll)
        stable_ready = 0
        while not self._stop_requested and loop.time() < deadline:
            activity = await self._read_page_activity_takeover()
            ready_state = str(activity.get("readyState") or "")
            busy = bool(activity.get("busy"))
            if not busy and ready_state in {"interactive", "complete"}:
                stable_ready += 1
                if stable_ready >= 2:
                    return True
            else:
                stable_ready = 0
            await asyncio.sleep(poll)
        return False

    async def _wait_for_state_change_takeover(self, previous_state: str, timeout: float = 4.0, poll: float = 0.06) -> str:
        poll = self._takeover_poll(poll, turbo=0.01)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, poll)
        last_state = previous_state
        while not self._stop_requested and loop.time() < deadline:
            try:
                state = await self._detect_takeover_state()
            except CDPError:
                await asyncio.sleep(poll)
                continue
            last_state = state
            if state != previous_state:
                return state
            await asyncio.sleep(poll)
        return last_state

    async def _grab_seats_takeover(self) -> list[str]:
        assert self.cdp is not None
        self._report("偵測到座位圖，先嘗試沿用威秀預配座位並直接下一步")
        if await self._click_best_button_takeover(["繼續", "下一步", "continue", "next"]):
            next_state = await self._wait_for_state_change_takeover("seat_selection", timeout=2.2, poll=0.05)
            if next_state != "seat_selection":
                self._report("已沿用威秀預配座位並前往下一步")
                return ["auto-assigned"]
            await self._wait_for_page_ready_takeover(timeout=1.8, poll=0.05)
        if await self._click_best_button_takeover(["繼續", "下一步", "continue", "next"]):
            next_state = await self._wait_for_state_change_takeover("seat_selection", timeout=1.6, poll=0.05)
            if next_state != "seat_selection":
                return ["auto-assigned"]

        self._report("未能直接沿用預配座位，改為嘗試自行選位")
        seats_info = await self.cdp.evaluate(READ_SEATS_JS)
        available = self._filter_available_seats(list(seats_info or []))
        best = self._pick_best_seats(
            available,
            count=self.event.ticket_count or 2,
            preference=self.config.vieshow.seat_preference or "center",
        )
        if not best:
            self._report("目前沒有符合條件的座位")
            return []

        picked_ids: list[str] = []
        for seat in best:
            target = (float(seat["x"]), float(seat["y"]))
            await self._move_mouse_takeover(target, duration_ms_range=(45, 95))
            await self._click_point_takeover(*target)
            picked_ids.append(str(seat.get("id") or seat.get("text") or seat.get("index")))
            await asyncio.sleep(0.004 if self._is_takeover_turbo() else random.uniform(0.01, 0.04))

        self._report(f"已選座: {', '.join(picked_ids)}")
        await self._click_best_button_takeover(["繼續", "下一步", "continue", "next"])
        return picked_ids

    async def _read_ticket_controls_takeover(self) -> dict[str, Any]:
        assert self.cdp is not None
        controls = await self.cdp.evaluate(READ_TICKET_CONTROLS_JS)
        return dict(controls or {})

    def _same_control(self, control: dict[str, Any], reference: dict[str, Any]) -> bool:
        control_name = str(control.get("name") or "").strip().lower()
        reference_name = str(reference.get("name") or "").strip().lower()
        if control_name and reference_name and control_name == reference_name:
            return True

        try:
            if int(control.get("index", -1)) == int(reference.get("index", -2)):
                return True
        except (TypeError, ValueError):
            pass

        control_label = str(control.get("label") or "").strip().lower()
        reference_label = str(reference.get("label") or "").strip().lower()
        return bool(control_label and reference_label and control_label == reference_label)

    def _control_text_blob(self, control: dict[str, Any]) -> str:
        parts = [
            str(control.get("rowText") or ""),
            str(control.get("label") or ""),
            str(control.get("name") or ""),
        ]
        parts.extend(str(item) for item in (control.get("headers") or []))
        return " ".join(part for part in parts if part).lower()

    def _is_visible_control(self, control: dict[str, Any]) -> bool:
        if "visible" in control and not bool(control.get("visible")):
            return False
        return float(control.get("y") or 0) > 0

    def _choose_ticket_select_takeover(
        self,
        controls: dict[str, Any],
        *,
        row_keywords: list[str],
        row_exclude_keywords: list[str],
        section_label: str,
        require_visible: bool,
    ) -> dict[str, Any] | None:
        section_lower = section_label.lower().strip()
        lowered_row_keywords = [keyword.lower() for keyword in row_keywords]
        lowered_excludes = [keyword.lower() for keyword in row_exclude_keywords]
        candidates = []
        for control in list(controls.get("selects") or []):
            if require_visible and not self._is_visible_control(control):
                continue
            blob = self._control_text_blob(control)
            if section_lower and section_lower not in blob:
                continue
            row_text = str(control.get("rowText") or "").lower()
            label_text = str(control.get("label") or "").lower()
            name_text = str(control.get("name") or "").lower()
            semantic_space = " ".join(part for part in [row_text, label_text, name_text] if part)
            search_space = " ".join(part for part in [semantic_space, blob] if part)

            matches = [keyword for keyword in lowered_row_keywords if keyword in search_space]
            if not matches:
                continue
            if any(keyword in semantic_space for keyword in lowered_excludes):
                continue
            score = (
                1 if self._is_visible_control(control) else 0,
                1 if any(row_text.startswith(keyword) or label_text.startswith(keyword) for keyword in matches) else 0,
                1 if any(name_text.startswith(keyword) for keyword in matches) else 0,
                max(len(keyword) for keyword in matches),
                -float(control.get("y") or 0),
            )
            candidates.append((score, control))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    async def _scroll_viewport_to_target(self, target_y: float, viewport_height: float) -> None:
        assert self.cdp is not None
        if viewport_height <= 0:
            return

        top_margin = 90.0
        bottom_margin = max(top_margin + 40.0, viewport_height - 120.0)
        if top_margin <= target_y <= bottom_margin:
            return

        anchor_x = max(120.0, min(self._last_mouse_pos[0], 900.0))
        anchor_y = max(120.0, min(self._last_mouse_pos[1], max(160.0, viewport_height - 120.0)))
        if target_y > bottom_margin:
            delta_y = min(820.0, max(240.0, target_y - viewport_height * 0.65))
        else:
            delta_y = -min(820.0, max(240.0, top_margin - target_y))

        await self.cdp.dispatch_mouse_wheel(anchor_x, anchor_y, delta_y=delta_y)
        await asyncio.sleep(0.008 if self._is_takeover_turbo() else random.uniform(0.02, 0.06))

    async def _find_scrollable_control_takeover(
        self,
        kind: str,
        matcher: Callable[[dict[str, Any]], bool],
        *,
        max_scrolls: int = 8,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        controls = await self._read_ticket_controls_takeover()
        for _ in range(max_scrolls + 1):
            items = list(controls.get(kind) or [])
            target = next((item for item in items if matcher(item)), None)
            if target is None:
                return controls, None

            viewport_height = float(controls.get("viewportHeight") or 0)
            target_y = float(target.get("y") or 0)
            if viewport_height <= 0 or 90.0 <= target_y <= max(130.0, viewport_height - 110.0):
                return controls, target

            await self._scroll_viewport_to_target(target_y, viewport_height)
            controls = await self._read_ticket_controls_takeover()

        items = list(controls.get(kind) or [])
        return controls, next((item for item in items if matcher(item)), None)

    async def _click_best_button_takeover(self, keywords: list[str]) -> bool:
        lowered = [keyword.lower() for keyword in keywords]
        controls = await self._read_ticket_controls_takeover()
        for _ in range(9):
            candidates: list[tuple[tuple[int, int, float, float], dict[str, Any]]] = []
            for button in list(controls.get("buttons") or []):
                label = str(button.get("label") or "").strip().lower()
                if not label:
                    continue
                if bool(button.get("disabled")):
                    continue
                if str(button.get("pointerEvents") or "").strip().lower() == "none":
                    continue
                matches = [keyword for keyword in lowered if keyword and keyword in label]
                if not matches:
                    continue

                exact_bonus = 1 if any(label == keyword for keyword in matches) else 0
                prefix_bonus = 1 if any(label.startswith(keyword) for keyword in matches) else 0
                longest_match = max(len(keyword) for keyword in matches)
                score = (
                    exact_bonus,
                    prefix_bonus,
                    float(longest_match),
                    float(button.get("y") or 0),
                    float(button.get("x") or 0),
                )
                candidates.append((score, button))

            if not candidates:
                return False

            _, button = max(candidates, key=lambda item: item[0])
            viewport_height = float(controls.get("viewportHeight") or 0)
            target_y = float(button.get("y") or 0)
            if viewport_height > 0 and not (90.0 <= target_y <= max(130.0, viewport_height - 110.0)):
                await self._scroll_viewport_to_target(target_y, viewport_height)
                controls = await self._read_ticket_controls_takeover()
                continue

            assert self.cdp is not None
            x = float(button["x"])
            y = float(button["y"])
            await self._move_mouse_takeover((x, y), duration_ms_range=(35, 90))
            await self._click_point_takeover(x, y)
            return True

        return False

    async def _wait_ticket_continue_ready_takeover(self, timeout: float = 2.4, poll: float = 0.05) -> bool:
        """Wait until ticket page continue button becomes enabled, or state changes."""

        continue_keywords = ["繼續", "下一步", "選擇座位", "前往選位", "continue", "next"]
        lowered = [keyword.lower() for keyword in continue_keywords]
        poll = self._takeover_poll(poll, turbo=0.01)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, poll)

        while not self._stop_requested and loop.time() < deadline:
            try:
                state = await self._detect_takeover_state()
            except CDPError:
                state = "unknown"
            if state != "ticket_type":
                return True

            try:
                controls = await self._read_ticket_controls_takeover()
            except CDPError:
                controls = {"buttons": []}

            for button in list(controls.get("buttons") or []):
                label = str(button.get("label") or "").strip().lower()
                if not label:
                    continue
                if not any(keyword in label for keyword in lowered):
                    continue
                if bool(button.get("disabled")):
                    continue
                if str(button.get("pointerEvents") or "").strip().lower() == "none":
                    continue
                return True

            try:
                activity = await self._read_page_activity_takeover()
            except CDPError:
                activity = {"busy": True}
            if bool(activity.get("busy")):
                await self._wait_for_page_ready_takeover(timeout=1.2, poll=poll)
            await asyncio.sleep(poll)
        return False

    async def _advance_ticket_type_takeover(self, attempts: int = 10) -> bool:
        """Advance from ticket_type page and verify state actually changes."""

        sleep_between = 0.01 if self._is_takeover_turbo() else 0.05
        wait_poll = 0.01 if self._is_takeover_turbo() else 0.03
        wait_timeout = 1.0 if self._is_takeover_turbo() else 1.8
        continue_keywords = ["繼續", "下一步", "選擇座位", "前往選位", "continue", "next"]

        await self._wait_ticket_continue_ready_takeover(
            timeout=1.1 if self._is_takeover_turbo() else 2.2,
            poll=wait_poll,
        )

        for _ in range(max(1, attempts)):
            clicked = await self._click_best_button_takeover(continue_keywords)
            if clicked:
                next_state = await self._wait_for_state_change_takeover(
                    "ticket_type",
                    timeout=wait_timeout,
                    poll=wait_poll,
                )
                if next_state != "ticket_type":
                    return True

            try:
                state = await self._detect_takeover_state()
            except CDPError:
                state = "unknown"

            if state != "ticket_type":
                return True

            if state == "error":
                await self._handle_error_takeover()

            try:
                activity = await self._read_page_activity_takeover()
            except CDPError:
                activity = {"busy": True}
            if bool(activity.get("busy")):
                await self._wait_for_page_ready_takeover(timeout=1.4, poll=wait_poll)
            else:
                await self._wait_ticket_continue_ready_takeover(timeout=0.8, poll=wait_poll)

            await asyncio.sleep(sleep_between)
        return False

    async def _expand_ticket_section_takeover(self, section_label: str) -> dict[str, Any]:
        controls = await self._read_ticket_controls_takeover()
        section_lower = section_label.lower()
        visible_in_section = [
            control
            for control in list(controls.get("selects") or [])
            if self._is_visible_control(control) and section_lower in self._control_text_blob(control)
        ]
        if visible_in_section:
            return controls

        clicked = await self._click_best_button_takeover([section_label])
        if clicked:
            self._report(f"已展開票種區塊: {section_label}")
            await asyncio.sleep(0.02 if self._is_takeover_turbo() else random.uniform(0.08, 0.16))
            controls = await self._read_ticket_controls_takeover()
        return controls

    async def _dispatch_named_key(self, key: str) -> None:
        assert self.cdp is not None
        mapping = {
            "ArrowDown": ("ArrowDown", "ArrowDown", 40),
            "ArrowUp": ("ArrowUp", "ArrowUp", 38),
            "Enter": ("Enter", "Enter", 13),
            "Home": ("Home", "Home", 36),
        }
        key_name, code, vk = mapping[key]
        await self.cdp.dispatch_key_event("keyDown", key=key_name, code=code, windows_virtual_key_code=vk)
        await asyncio.sleep(0.005 if self._is_takeover_turbo() else 0.02)
        await self.cdp.dispatch_key_event("keyUp", key=key_name, code=code, windows_virtual_key_code=vk)

    async def _set_select_value_takeover(self, control: dict[str, Any], desired_count: int) -> bool:
        _, current_control = await self._find_scrollable_control_takeover(
            "selects",
            lambda candidate: self._same_control(candidate, control),
        )
        if current_control is None:
            return False

        control = current_control
        options = list(control.get("options") or [])
        if not options:
            self._report("票種下拉選單沒有可用選項")
            return False

        count_to_index: dict[int, int] = {}
        for option in options:
            text = str(option.get("text") or "")
            value = str(option.get("value") or "")
            index = int(option.get("index", 0))
            stripped = text.strip()
            digits_in_text = "".join(ch for ch in stripped if ch.isdigit())
            candidates: list[int] = []
            if value.isdigit():
                candidates.append(int(value))
            if stripped.isdigit():
                candidates.append(int(stripped))
            if digits_in_text.isdigit():
                candidates.append(int(digits_in_text))
            for parsed_count in candidates:
                count_to_index.setdefault(parsed_count, index)

        target_count = desired_count if desired_count in count_to_index else None
        if target_count is None:
            valid_counts = sorted(count for count in count_to_index.keys() if count > 0)
            if valid_counts:
                smaller_or_equal = [count for count in valid_counts if count <= desired_count]
                target_count = (smaller_or_equal[-1] if smaller_or_equal else valid_counts[-1])
                if target_count != desired_count:
                    self._report(f"票種數量 {desired_count} 目前不可選，改用可選數量 {target_count}")

        if target_count is None:
            option_preview = [str(option.get("text") or option.get("value") or "") for option in options[:8]]
            self._report(f"票種數量設定失敗：目標 {desired_count}，可見選項 {option_preview}")
            return False

        target_index = count_to_index.get(target_count)
        if target_index is None:
            self._report(f"票種數量設定失敗：找不到對應 index（count={target_count}）")
            return False

        x = float(control["x"])
        y = float(control["y"])
        assert self.cdp is not None
        await self._move_mouse_takeover((x, y), duration_ms_range=(35, 90))
        await self._click_point_takeover(x, y)

        current_index = int(control.get("selectedIndex", 0))
        if target_index < current_index:
            await self._dispatch_named_key("Home")
            current_index = 0
        for _ in range(max(0, target_index - current_index)):
            await asyncio.sleep(0.004 if self._is_takeover_turbo() else 0.015)
            await self._dispatch_named_key("ArrowDown")
        await asyncio.sleep(0.004 if self._is_takeover_turbo() else 0.015)
        await self._dispatch_named_key("Enter")
        await asyncio.sleep(0.01 if self._is_takeover_turbo() else 0.04)

        verify_controls = await self._read_ticket_controls_takeover()
        for candidate in list(verify_controls.get("selects") or []):
            if self._same_control(candidate, control):
                if int(candidate.get("selectedIndex", -1)) == target_index:
                    return True
        verify_by_point = await self.cdp.evaluate(
            f"""
            (() => {{
                let node = document.elementFromPoint({x}, {y});
                if (node && node.tagName !== 'SELECT') {{
                    node = node.closest('select');
                }}
                if (!node || node.tagName !== 'SELECT') return null;
                return node.selectedIndex;
            }})()
            """
        )
        if isinstance(verify_by_point, int) and verify_by_point == target_index:
            return True

        assert self.cdp is not None
        fallback_changed = await self.cdp.evaluate(
            f"""
            (() => {{
                const controls = Array.from(document.querySelectorAll('select'));
                const name = {json.dumps(str(control.get("name") or ""))};
                const fallbackIndex = {int(control.get("index", -1))};
                let target = null;
                if (name) {{
                    target = controls.find(node => node.name === name || node.id === name) || null;
                }}
                if (!target && fallbackIndex >= 0 && fallbackIndex < controls.length) {{
                    target = controls[fallbackIndex];
                }}
                if (!target) return false;
                target.selectedIndex = {target_index};
                target.dispatchEvent(new Event('input', {{ bubbles: true }}));
                target.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return true;
            }})()
            """
        )
        if not fallback_changed:
            fallback_changed = await self.cdp.evaluate(
                f"""
                (() => {{
                    const desired = {int(desired_count)};
                    const x = {x};
                    const y = {y};
                    let target = document.elementFromPoint(x, y);
                    if (target && target.tagName !== 'SELECT') {{
                        target = target.closest('select');
                    }}
                    if (!target || target.tagName !== 'SELECT') return false;
                    const options = Array.from(target.options || []);
                    const pickIndex = options.findIndex((option) => {{
                        const text = (option.textContent || '').trim();
                        const value = String(option.value || '');
                        const digits = text.replace(/\\D+/g, '');
                        return text === String(desired) || value === String(desired) || digits === String(desired);
                    }});
                    if (pickIndex < 0) return false;
                    target.selectedIndex = pickIndex;
                    target.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }})()
                """
            )
        if not fallback_changed:
            return False

        verify_controls = await self._read_ticket_controls_takeover()
        for candidate in list(verify_controls.get("selects") or []):
            if self._same_control(candidate, control):
                if int(candidate.get("selectedIndex", -1)) == target_index:
                    return True
        verify_by_point = await self.cdp.evaluate(
            f"""
            (() => {{
                let node = document.elementFromPoint({x}, {y});
                if (node && node.tagName !== 'SELECT') {{
                    node = node.closest('select');
                }}
                if (!node || node.tagName !== 'SELECT') return null;
                return node.selectedIndex;
            }})()
            """
        )
        if isinstance(verify_by_point, int) and verify_by_point == target_index:
            return True
        return False

    async def _enter_booking_flow_takeover(self) -> bool:
        controls = await self._read_ticket_controls_takeover()
        option_clicked = await self._click_primary_booking_option_takeover()
        if option_clicked:
            await self._wait_for_page_ready_takeover(timeout=2.2, poll=0.05)
            controls = await self._read_ticket_controls_takeover()

        for checkbox in controls.get("checkboxes", []):
            label = str(checkbox.get("label") or "")
            if checkbox.get("checked"):
                continue
            if any(flag in label for flag in ["同意", "規定", "條款", "agree"]):
                assert self.cdp is not None
                x = float(checkbox["x"])
                y = float(checkbox["y"])
                await self._move_mouse_takeover((x, y), duration_ms_range=(35, 80))
                await self._click_point_takeover(x, y)

        clicked = await self._click_best_button_takeover(["前往訂票"])
        if clicked:
            self._report("已勾選規定並從 booking.aspx 進入票種頁")
            await self._wait_for_state_change_takeover("booking_landing", timeout=3.0, poll=0.05)
            return True

        self._report("已到 booking.aspx 規定頁，但找不到可點的前往訂票按鈕")
        return False

    async def _select_ticket_type_takeover(self) -> bool:
        ticket_type = self.config.vieshow.ticket_type or "full"
        row_keywords = _ticket_row_keywords(ticket_type)
        row_exclude_keywords = _ticket_row_exclude_keywords(ticket_type)
        section_label = _ticket_section_for_type(ticket_type)

        controls = await self._expand_ticket_section_takeover(section_label)
        target_select = self._choose_ticket_select_takeover(
            controls,
            row_keywords=row_keywords,
            row_exclude_keywords=row_exclude_keywords,
            section_label=section_label,
            require_visible=True,
        )
        if target_select is None:
            controls = await self._read_ticket_controls_takeover()
            target_select = self._choose_ticket_select_takeover(
                controls,
                row_keywords=row_keywords,
                row_exclude_keywords=row_exclude_keywords,
                section_label="",
                require_visible=True,
            )
        if target_select is None:
            controls = await self._read_ticket_controls_takeover()
            target_select = self._choose_ticket_select_takeover(
                controls,
                row_keywords=row_keywords,
                row_exclude_keywords=row_exclude_keywords,
                section_label="",
                require_visible=False,
            )

        desired_count = self.event.ticket_count or 2
        selected = False
        if target_select is not None:
            selected = await self._set_select_value_takeover(target_select, desired_count)

        if not selected:
            for _ in range(2):
                await self._wait_for_page_ready_takeover(timeout=0.7, poll=0.05)
                controls = await self._read_ticket_controls_takeover()
                retry_target = self._choose_ticket_select_takeover(
                    controls,
                    row_keywords=row_keywords,
                    row_exclude_keywords=row_exclude_keywords,
                    section_label="",
                    require_visible=False,
                )
                if retry_target is None:
                    continue
                selected = await self._set_select_value_takeover(retry_target, desired_count)
                if selected:
                    break

        for checkbox in controls.get("checkboxes", []):
            label = str(checkbox.get("label") or "")
            if checkbox.get("checked"):
                continue
            if any(flag in label for flag in ["同意", "規定", "條款", "agree"]):
                assert self.cdp is not None
                x = float(checkbox["x"])
                y = float(checkbox["y"])
                await self._move_mouse_takeover((x, y), duration_ms_range=(35, 80))
                await self._click_point_takeover(x, y)

        if not selected:
            self._report(f"票種設定失敗：{section_label} / {ticket_type} x {desired_count}，停在票種頁等待重試")
            return False

        await self._wait_for_page_ready_takeover(timeout=1.2, poll=0.03)
        advanced = await self._advance_ticket_type_takeover(attempts=10)
        if not advanced:
            self._report(f"已選到票種但無法前進（可能按鈕被鎖或被擋）：{section_label} / {ticket_type} x {desired_count}")
            return False

        self._report(f"已在 {section_label} 選擇 {ticket_type} x {desired_count}，準備進入座位圖")
        return True

    async def _handle_error_takeover(self) -> None:
        assert self.cdp is not None
        info = await self.cdp.evaluate(READ_ERROR_JS)
        if not info:
            return
        message = str(info.get("message") or "unknown error")
        self._update_sale_time_from_message_takeover(message)
        self._report(f"威秀彈窗: {message}")
        button = info.get("button")
        if button:
            await self.cdp.dispatch_click(float(button["x"]), float(button["y"]))

    async def _handle_checkout_takeover(self) -> None:
        assert self.cdp is not None
        info = await self.cdp.evaluate(READ_CHECKOUT_JS)
        total = str((info or {}).get("total") or "")
        self.last_success_info = f"已進入付款頁面，金額: NT${total}" if total else "已進入付款頁面"
        self._report("已進入付款頁面，接手流程結束，請手動完成付款")
        if total:
            self._report(f"訂單總金額: NT${total}")

    async def _click_primary_booking_option_takeover(self) -> bool:
        controls = await self._read_ticket_controls_takeover()
        labels = " ".join(str(button.get("label") or "").lower() for button in list(controls.get("buttons") or []))
        option_markers = [
            "線上即時付款",
            "一般 / 銀行優惠",
            "會員票種",
            "general",
            "bank privilege",
            "svc discount",
            "corporate movie money",
        ]
        if not any(marker.lower() in labels for marker in option_markers):
            return False

        clicked = await self._click_best_button_takeover(
            ["線上即時付款", "一般 / 銀行優惠", "會員票種", "general", "bank privilege", "svc discount"]
        )
        if clicked:
            self._report("已在方案頁預設點選上方選項（線上即時付款）。")
        return clicked

    async def _rush_quick_booking_takeover(self) -> bool:
        sale_time = self._parse_sale_time_takeover()
        now = self._now_local()
        burst_click_loops = 20 if self._is_takeover_turbo() else 10
        inter_click_sleep = 0.008 if self._is_takeover_turbo() else 0.04
        post_click_sleep = 0.004 if self._is_takeover_turbo() else 0.02
        if sale_time is not None:
            seconds_left = (sale_time - now).total_seconds()
            if seconds_left > 1.2:
                bucket = int(seconds_left)
                if bucket != self._sale_countdown_reported:
                    self._sale_countdown_reported = bucket
                    self._report(f"距離開賣尚有 {bucket}s")
                max_wait = 0.35 if self._is_takeover_turbo() else 0.7
                min_wait = 0.08 if self._is_takeover_turbo() else 0.2
                await asyncio.sleep(min(max_wait, max(min_wait, seconds_left - 1.0)))
                return False
            if seconds_left > 0:
                await asyncio.sleep(max(0.01, seconds_left - 0.02))

        if not self._sale_burst_reported:
            self._sale_burst_reported = True
            self._report("進入開賣窗口，開始高速點擊。")

        for _ in range(burst_click_loops):
            clicked_showtime = await self._click_default_showtime_takeover()
            if clicked_showtime:
                await asyncio.sleep(post_click_sleep)
                state_after_showtime = await self._detect_takeover_state()
                if state_after_showtime in {"booking_landing", "ticket_type", "seat_selection", "checkout"}:
                    return True
                if state_after_showtime == "error":
                    await self._handle_error_takeover()
                    seconds_after_error = self._seconds_until_sale_takeover()
                    if seconds_after_error is not None and seconds_after_error > 0:
                        return False
                    await asyncio.sleep(inter_click_sleep)
                    continue

            clicked = await self._click_best_button_takeover(
                ["前往訂票", "立即訂票", "查看座位", "查詢座位", "繼續", "下一步", "continue", "next"]
            )
            if clicked:
                await asyncio.sleep(post_click_sleep)

            state = await self._detect_takeover_state()
            if state in {"booking_landing", "ticket_type", "seat_selection", "checkout"}:
                return True
            if state == "error":
                await self._handle_error_takeover()
                seconds_after_error = self._seconds_until_sale_takeover()
                if seconds_after_error is not None and seconds_after_error > 0:
                    return False
            await asyncio.sleep(inter_click_sleep)

        return False

    async def _enter_booking_flow_takeover(self) -> bool:
        controls = await self._read_ticket_controls_takeover()
        option_clicked = await self._click_primary_booking_option_takeover()
        if option_clicked:
            await self._wait_for_page_ready_takeover(timeout=2.2, poll=0.05)
            controls = await self._read_ticket_controls_takeover()

        for checkbox in controls.get("checkboxes", []):
            label = str(checkbox.get("label") or "")
            if checkbox.get("checked"):
                continue
            if any(flag in label for flag in ["同意", "規定", "條款", "agree"]):
                assert self.cdp is not None
                x = float(checkbox["x"])
                y = float(checkbox["y"])
                await self._move_mouse_takeover((x, y), duration_ms_range=(35, 80))
                await self._click_point_takeover(x, y)

        clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        if not clicked:
            await self._wait_for_page_ready_takeover(timeout=1.8, poll=0.05)
            clicked = await self._click_best_button_takeover(["前往訂票", "立即訂票", "繼續", "下一步", "continue", "next"])
        if clicked:
            self._report("已完成 booking 頁面進入步驟。")
            await self._wait_for_state_change_takeover("booking_landing", timeout=3.0, poll=0.05)
            return True

        self._report("booking 頁面找不到可用的前進按鈕。")
        return False

    async def _select_theater_legacy(self) -> None:
        assert self.page is not None
        target = (self.config.vieshow.theater_code or self.config.vieshow.theater_keyword or "").strip()
        if not target:
            self._report("未設定影城，等待使用者手動選擇")
            await self.page.sleep(1.0)
            return

        result = await self.page.evaluate(
            f"""
            (() => {{
                const target = {json.dumps(target)};
                const select = document.querySelector('#theater');
                if (!select) return false;
                const option = Array.from(select.options).find(option => (
                    option.value === target ||
                    option.textContent.includes(target)
                ));
                if (!option) return false;
                select.value = option.value;
                select.dispatchEvent(new Event('change', {{bubbles: true}}));
                const button = document.querySelector('#show_movie_button, button, input[type="submit"]');
                if (button) button.click();
                return true;
            }})()
            """
        )
        if result:
            self._report(f"已選擇影城: {target}")
            await self.page.sleep(1.2)
        else:
            self._report(f"找不到影城: {target}")
            await self.page.sleep(1.0)

    async def _select_movie_showtime_legacy(self) -> None:
        assert self.page is not None
        movie_keyword = self.config.vieshow.movie_keyword or self.event.name
        showtime_keyword = self.config.vieshow.showtime_keyword or self.event.date_keyword
        result = await self.page.evaluate(
            f"""
            (() => {{
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const movieKeyword = normalize({json.dumps(movie_keyword)});
                const showtimeKeyword = normalize({json.dumps(showtime_keyword)});
                const movieNodes = Array.from(document.querySelectorAll('a, button, h2, h3, span, div'))
                    .filter(node => normalize(node.textContent).includes(movieKeyword));
                if (movieNodes.length === 0) return {{found: false, error: 'movie_not_found'}};

                const root = movieNodes[0].closest('[class*="movie"], [class*="film"], li, tr, .item, .panel, .card')
                    || movieNodes[0].parentElement
                    || document.body;
                const showtimeNodes = Array.from(root.querySelectorAll('a, button, span, div, td'))
                    .filter(node => {{
                        const text = normalize(node.textContent || node.value || '');
                        return !!text && (!showtimeKeyword || text.includes(showtimeKeyword));
                    }});

                if (showtimeNodes.length > 0) {{
                    showtimeNodes[0].click();
                    return {{found: true}};
                }}

                const clickTarget = movieNodes[0].closest('a, button') || movieNodes[0];
                clickTarget.click();
                return {{found: true}};
            }})()
            """
        )
        if result and result.get("found"):
            self._report("已選擇電影 / 場次")
            await self.page.sleep(1.5)
        else:
            self._report("找不到對應的電影或場次")
            await self.page.sleep(1.0)

    async def _enter_booking_flow_legacy(self) -> None:
        assert self.page is not None
        use_group = self.config.vieshow.ticket_type in {"senior", "love"}
        clicked = await self.page.evaluate(
            f"""
            (() => {{
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();

                for (const cb of document.querySelectorAll('input[type="checkbox"]')) {{
                    const text = normalize((cb.closest('label, tr, li, div, form') || cb.parentElement || cb).innerText || '');
                    if (!cb.checked && /(同意|agree|條款|規定)/i.test(text)) {{
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', {{bubbles: true}}));
                        cb.dispatchEvent(new Event('input', {{bubbles: true}}));
                    }}
                }}

                const proceedKeywords = ['前往訂票', '訂票', '繼續', '下一步', 'confirm', 'continue', 'next'];
                const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], a, [role="button"]'));
                const action = buttons.find(node => {{
                    const text = normalize(node.textContent || node.value || '');
                    const visible = !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                    return visible && !node.disabled && proceedKeywords.some(keyword => text.includes(normalize(keyword)));
                }});
                if (action) {{
                    action.click();
                    return true;
                }}

                const selector = {json.dumps('a[href="#bookGroup"], .icon-vsgroup' if use_group else 'a[href="#bookNormal"], .icon-vsgeneral')};
                const node = document.querySelector(selector);
                if (!node) return false;
                node.click();
                return true;
            }})()
            """
        )
        if clicked:
            self._report("已進入訂票入口")
            await self.page.sleep(1.5)

    async def _select_seats_legacy(self) -> None:
        assert self.page is not None
        seats = await self.page.evaluate(READ_SEATS_JS)
        available = self._filter_available_seats(list(seats or []))
        best = self._pick_best_seats(
            available,
            count=self.event.ticket_count or 2,
            preference=self.config.vieshow.seat_preference or "center",
        )
        if not best:
            self._report("目前沒有可選座位")
            await self.page.sleep(1.0)
            return

        seat_ids = [str(seat.get("id") or "") for seat in best if seat.get("id")]
        indexes = [int(seat.get("index", 0)) for seat in best]
        picked = await self.page.evaluate(f"({LEGACY_SELECT_SEATS_JS})({json.dumps(seat_ids)}, {json.dumps(indexes)})")
        picked = list(picked or [])
        if picked:
            self._report(f"已選座: {', '.join(picked)}")
            await self.page.sleep(0.5)
            await self._click_confirm_button_legacy()
        else:
            self._report("選位失敗")

    async def _click_confirm_button_legacy(self) -> None:
        assert self.page is not None
        clicked = await self.page.evaluate(
            """
            (() => {
                const buttons = Array.from(document.querySelectorAll(
                    'button, input[type="submit"], input[type="button"], a[class*="btn"], [role="button"]'
                ));
                const keywords = ['確認', '前往訂票', 'next', 'confirm', 'continue', '下一步', '送出'];
                const node = buttons.find(button => {
                    const text = (button.textContent || button.value || '').toLowerCase();
                    const visible = !!(button.offsetWidth || button.offsetHeight || button.getClientRects().length);
                    return visible && !button.disabled && keywords.some(keyword => text.includes(keyword.toLowerCase()));
                });
                if (!node) return false;
                node.click();
                return true;
            })()
            """
        )
        if clicked:
            self._report("已點擊確認按鈕")
            await self.page.sleep(1.0)

    async def _select_ticket_type_legacy(self) -> None:
        assert self.page is not None
        keywords = _ticket_type_keywords(self.config.vieshow.ticket_type or "full")
        result = await self.page.evaluate(
            f"({LEGACY_SELECT_TICKET_JS})({json.dumps(keywords)}, {self.event.ticket_count or 2})"
        )
        if result and result.get("matched"):
            self._report(f"已選票種: {self.config.vieshow.ticket_type} x {self.event.ticket_count or 2}")
        else:
            self._report("未找到對應票種，請手動確認")
        await self.page.sleep(0.5)
        await self._click_confirm_button_legacy()

    async def _login_ishow_legacy(self) -> None:
        assert self.page is not None
        if not (self.config.vieshow.auto_login and self.config.vieshow.ishow_email and self.config.vieshow.ishow_password):
            self._report("需要登入 iShow，請手動登入")
            await self.page.sleep(1.0)
            return

        email = await self.page.select('input[type="email"], input[name*="email" i], input[id*="email" i]')
        password = await self.page.select('input[type="password"]')
        submit = await self.page.select('button[type="submit"], input[type="submit"]')
        if not (email and password and submit):
            self._report("找不到 iShow 登入表單")
            await self.page.sleep(1.0)
            return

        await email.send_keys(self.config.vieshow.ishow_email)
        await asyncio.sleep(click_delay())
        await password.send_keys(self.config.vieshow.ishow_password)
        await asyncio.sleep(click_delay())
        await submit.click()
        self._report("已送出 iShow 登入表單")
        await self.page.sleep(2.0)

    async def _handle_checkout_legacy(self) -> None:
        assert self.page is not None
        info = await self.page.evaluate(READ_CHECKOUT_JS)
        total = str((info or {}).get("total") or "")
        self.last_success_info = f"已到達結帳頁面，金額: NT${total}" if total else "已到達結帳頁面"
        self._report("已進入結帳頁面，請手動完成付款")
        if total:
            self._report(f"訂單總金額: NT${total}")

    async def _handle_error_legacy(self) -> None:
        assert self.page is not None
        info = await self.page.evaluate(READ_ERROR_JS)
        if not info:
            return
        message = str(info.get("message") or "unknown error")
        self._report(f"威秀彈窗: {message}")
        button = info.get("button")
        if button:
            await self.page.evaluate(
                f"""
                (() => {{
                    const node = document.elementFromPoint({float(button["x"])}, {float(button["y"])});
                    if (node) node.click();
                }})()
                """
            )
        await self.page.sleep(0.5)


__all__ = ["VieShowBot", "VIESHOW_TICKET_URL"]
