"""Raw CDP takeover helpers for a user-owned Chrome instance.

This module intentionally avoids Playwright/Nodriver abstractions. It connects
to Chrome DevTools Protocol directly via the HTTP discovery endpoint and a
minimal WebSocket client implemented with the standard library.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import base64
import hashlib
import http.client
import json
import os
import random
import struct
from typing import Any
from urllib.parse import urlparse

from ticket_bot.human import click_delay, typing_delays


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass(slots=True)
class CDPTarget:
    """Target metadata returned by Chrome remote debugging discovery."""

    id: str
    title: str
    url: str
    type: str
    web_socket_debugger_url: str


class CDPError(RuntimeError):
    """Raised when the CDP transport or target selection fails."""


class _WebSocketClient:
    """Minimal async WebSocket client for ws:// Chrome CDP endpoints."""

    def __init__(self, url: str):
        self._url = url
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @staticmethod
    def _split_url(url: str) -> tuple[str, int, str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"ws", "http"}:
            raise CDPError(f"Unsupported websocket URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (80 if parsed.scheme in {"ws", "http"} else 443)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return host, port, path

    async def connect(self, handshake_key: str | None = None) -> None:
        host, port, path = self._split_url(self._url)
        if self._reader is None or self._writer is None:
            self._reader, self._writer = await asyncio.open_connection(host, port)

        key = handshake_key or base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._writer.write(request.encode("ascii"))
        await self._writer.drain()

        response = await self._reader.readuntil(b"\r\n\r\n")
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"WebSocket handshake failed: {response!r}")

        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest())
        if accept not in response:
            raise CDPError("WebSocket handshake verification failed")

    async def close(self) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write(b"\x88\x00")
            await self._writer.drain()
        except Exception:
            pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass
        self._reader = None
        self._writer = None

    async def send_text(self, payload: str) -> None:
        if self._writer is None:
            raise CDPError("WebSocket is not connected")
        data = payload.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._writer.write(bytes(header) + mask + masked)
        await self._writer.drain()

    async def recv_text(self) -> str:
        if self._reader is None:
            raise CDPError("WebSocket is not connected")

        first = await self._reader.readexactly(2)
        fin = first[0] & 0x80
        opcode = first[0] & 0x0F
        masked = first[1] & 0x80
        length = first[1] & 0x7F
        if opcode == 0x8:
            raise EOFError("WebSocket closed")
        if length == 126:
            length = struct.unpack("!H", await self._reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await self._reader.readexactly(8))[0]
        mask = await self._reader.readexactly(4) if masked else b""
        payload = await self._reader.readexactly(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if not fin:
            raise CDPError("Fragmented WebSocket frames are not supported")
        if opcode == 0x1:
            return payload.decode("utf-8")
        if opcode == 0x2:
            return payload.decode("utf-8", errors="replace")
        return ""


def _http_get_json(endpoint: str) -> list[dict[str, Any]]:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise CDPError(f"Unsupported CDP discovery URL: {endpoint!r}")
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 80), timeout=5)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    conn.request("GET", path)
    resp = conn.getresponse()
    if resp.status != 200:
        raise CDPError(f"CDP discovery request failed: HTTP {resp.status}")
    body = resp.read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, list):
        raise CDPError("CDP discovery endpoint returned an unexpected payload")
    return data


class CDPTakeoverEngine:
    """Direct Chrome DevTools Protocol helper for takeover mode."""

    def __init__(self) -> None:
        self._ws: _WebSocketClient | None = None
        self._next_id = 0
        self._target: CDPTarget | None = None
        self._pending: dict[int, dict[str, Any]] = {}

    @property
    def target(self) -> CDPTarget | None:
        return self._target

    @staticmethod
    def _normalize_cdp_url(cdp_url: str) -> str:
        if cdp_url.startswith("ws://") or cdp_url.startswith("wss://"):
            return cdp_url
        if cdp_url.startswith("http://") or cdp_url.startswith("https://"):
            return cdp_url.rstrip("/")
        return f"http://{cdp_url.rstrip('/')}"

    @staticmethod
    def _pick_target(tabs: list[dict[str, Any]], page_url_substring: str = "") -> dict[str, Any]:
        if not isinstance(tabs, list):
            raise CDPError("CDP discovery endpoint returned a malformed target list")

        if page_url_substring:
            matches = [
                tab
                for tab in tabs
                if isinstance(tab, dict)
                and (page_url_substring in str(tab.get("url", "")) or page_url_substring in str(tab.get("title", "")))
            ]
            if matches:
                tabs = matches

        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            if tab.get("type") == "page" and tab.get("url") not in {"", "about:blank"}:
                return tab
        if tabs:
            first = tabs[0]
            if isinstance(first, dict):
                return first
        raise CDPError("No CDP targets found")

    async def connect(self, cdp_url: str = "http://127.0.0.1:9222", page_url_substring: str = "") -> CDPTarget:
        """Connect to an existing Chrome target selected from /json."""

        discovery_url = self._normalize_cdp_url(cdp_url)
        if discovery_url.startswith("ws://") or discovery_url.startswith("wss://"):
            ws_url = discovery_url
            target = CDPTarget(id="", title="", url="", type="page", web_socket_debugger_url=ws_url)
        else:
            tabs = _http_get_json(f"{discovery_url}/json")
            if not isinstance(tabs, list):
                raise CDPError("CDP discovery endpoint returned a malformed target list")
            picked = self._pick_target(tabs, page_url_substring=page_url_substring)
            ws_url = str(picked.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                raise CDPError("Selected target does not expose webSocketDebuggerUrl")
            target = CDPTarget(
                id=str(picked.get("id", "")),
                title=str(picked.get("title", "")),
                url=str(picked.get("url", "")),
                type=str(picked.get("type", "page")),
                web_socket_debugger_url=ws_url,
            )

        self._ws = _WebSocketClient(ws_url)
        await self._ws.connect()
        self._target = target
        return target

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        self._ws = None
        self._target = None
        self._pending.clear()

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise CDPError("CDP connection is not established")

        self._next_id += 1
        message_id = self._next_id
        await self._ws.send_text(json.dumps({"id": message_id, "method": method, "params": params or {}}))

        while True:
            raw = await self._ws.recv_text()
            if not raw:
                continue
            response = json.loads(raw)
            if response.get("id") == message_id:
                if "error" in response:
                    error = response["error"]
                    raise CDPError(error.get("message", "CDP command failed"))
                return response.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        result = await self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        value = result.get("result", {}).get("value")
        if value is None and isinstance(result.get("result"), dict):
            # Some CDP responses place the value directly on the inner result.
            value = result["result"].get("value")
        return value

    async def get_current_url(self) -> str:
        value = await self.evaluate("window.location.href")
        return str(value or "")

    async def dispatch_mouse_event(
        self,
        event_type: str,
        x: float,
        y: float,
        *,
        button: str = "left",
        click_count: int = 1,
        delta_x: float = 0,
        delta_y: float = 0,
    ) -> dict[str, Any]:
        return await self._send(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
                "deltaX": delta_x,
                "deltaY": delta_y,
            },
        )

    async def dispatch_key_event(
        self,
        event_type: str,
        *,
        text: str = "",
        key: str = "",
        code: str = "",
        windows_virtual_key_code: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"type": event_type}
        if text:
            params["text"] = text
        if key:
            params["key"] = key
        if code:
            params["code"] = code
        if windows_virtual_key_code is not None:
            params["windowsVirtualKeyCode"] = windows_virtual_key_code
        return await self._send("Input.dispatchKeyEvent", params)

    async def dispatch_click(self, x: float, y: float) -> None:
        await self.dispatch_mouse_event("mouseMoved", x - 2, y - 1, click_count=0)
        await asyncio.sleep(click_delay())
        await self.dispatch_mouse_event("mouseMoved", x, y, click_count=0)
        await asyncio.sleep(click_delay())
        await self.dispatch_mouse_event("mousePressed", x, y, click_count=1)
        await asyncio.sleep(max(0.03, click_delay() / 2))
        await self.dispatch_mouse_event("mouseReleased", x, y, click_count=1)

    async def dispatch_mouse_wheel(self, x: float, y: float, *, delta_x: float = 0, delta_y: float = 0) -> None:
        await self.dispatch_mouse_event(
            "mouseWheel",
            x,
            y,
            button="none",
            click_count=0,
            delta_x=delta_x,
            delta_y=delta_y,
        )

    def _bezier_points(
        self,
        from_xy: tuple[float, float],
        to_xy: tuple[float, float],
        *,
        steps: int = 15,
        rng: random.Random | None = None,
    ) -> list[tuple[float, float]]:
        generator = rng or random.Random()
        x0, y0 = from_xy
        x3, y3 = to_xy
        dx = x3 - x0
        dy = y3 - y0
        x1 = x0 + dx * 0.3 + generator.uniform(-30, 30)
        y1 = y0 + dy * 0.1 + generator.uniform(-30, 30)
        x2 = x0 + dx * 0.7 + generator.uniform(-20, 20)
        y2 = y0 + dy * 0.9 + generator.uniform(-20, 20)

        points: list[tuple[float, float]] = []
        for index in range(steps + 1):
            t = index / steps
            x = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t**2 * x2 + t**3 * x3
            y = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t**2 * y2 + t**3 * y3
            points.append((x + generator.uniform(-1, 1), y + generator.uniform(-1, 1)))
        return points

    async def human_mouse_move(
        self,
        from_xy: tuple[float, float],
        to_xy: tuple[float, float],
        *,
        duration_ms: int = 300,
        steps: int = 15,
        rng: random.Random | None = None,
    ) -> list[tuple[float, float]]:
        """Move the mouse along a Bezier curve and return the sampled points."""

        if steps < 1:
            raise ValueError("steps must be >= 1")

        points = self._bezier_points(from_xy, to_xy, steps=steps, rng=rng)
        delay = max(0.001, duration_ms / 1000 / max(1, len(points)))
        for x, y in points:
            await self.dispatch_mouse_event("mouseMoved", x, y, click_count=0)
            await asyncio.sleep(delay)
        return points

    async def type_text(self, text: str, *, key_interval: float | None = None) -> None:
        """Type text with per-character delays derived from the human helpers."""

        delays = typing_delays(text)
        for char, delay in zip(text, delays):
            await self.dispatch_key_event("keyDown", text=char)
            await self.dispatch_key_event("char", text=char)
            await self.dispatch_key_event("keyUp", text=char)
            await asyncio.sleep(key_interval if key_interval is not None else delay)

    async def find_seats(self, seat_selector: str = '[class*="seat"]') -> list[dict[str, Any]]:
        """Collect seat metadata from the active page.

        The method returns center coordinates so the caller can feed them into
        the mouse dispatch helpers.
        """

        seats = await self.evaluate(
            f"""
            (() => {{
                const nodes = Array.from(document.querySelectorAll({json.dumps(seat_selector)}));
                return nodes.map((node, index) => {{
                    const rect = node.getBoundingClientRect();
                    return {{
                        index,
                        id: node.id || node.dataset.id || node.dataset.seat || '',
                        row: node.dataset.row || node.getAttribute('data-row') || '',
                        col: node.dataset.col || node.dataset.seat || node.getAttribute('data-col') || '',
                        text: (node.textContent || '').trim(),
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        width: rect.width,
                        height: rect.height,
                        disabled: !!(node.disabled || node.getAttribute('aria-disabled') === 'true')
                    }});
                }});
            }})()
            """
        )
        return list(seats or [])

    async def click_element(self, selector: str) -> bool:
        """Click an element using its bounding box and CDP mouse input."""

        result = await self.evaluate(
            f"""
            (() => {{
                const node = document.querySelector({json.dumps(selector)});
                if (!node) return null;
                const rect = node.getBoundingClientRect();
                return {{
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2
                }};
            }})()
            """
        )
        if not result:
            return False
        await self.dispatch_click(float(result["x"]), float(result["y"]))
        return True


__all__ = ["CDPError", "CDPTarget", "CDPTakeoverEngine"]
