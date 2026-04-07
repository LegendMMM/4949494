from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest

from ticket_bot.browser.cdp_takeover import CDPError, CDPTakeoverEngine, _WebSocketClient


class DummyWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class DummyReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    async def readexactly(self, n: int) -> bytes:
        chunk = self._data[self._offset : self._offset + n]
        if len(chunk) < n:
            raise EOFError
        self._offset += n
        return chunk

    async def readuntil(self, separator: bytes) -> bytes:
        idx = self._data.index(separator, self._offset) + len(separator)
        chunk = self._data[self._offset : idx]
        self._offset = idx
        return chunk


def _frame_text(text: str) -> bytes:
    payload = text.encode("utf-8")
    return bytes([0x81, len(payload)]) + payload


def test_pick_target_prefers_url_substring():
    tabs = [
        {"id": "1", "type": "page", "url": "about:blank", "title": "", "webSocketDebuggerUrl": "ws://a"},
        {"id": "2", "type": "page", "url": "https://example.com/vieshow", "title": "VieShow", "webSocketDebuggerUrl": "ws://b"},
    ]
    picked = CDPTakeoverEngine._pick_target(tabs, page_url_substring="vieshow")
    assert picked["id"] == "2"


def test_bezier_points_are_deterministic_with_seed():
    engine = CDPTakeoverEngine()
    rng = __import__("random").Random(42)
    pts_a = engine._bezier_points((0, 0), (100, 50), steps=5, rng=rng)
    rng = __import__("random").Random(42)
    pts_b = engine._bezier_points((0, 0), (100, 50), steps=5, rng=rng)
    assert pts_a == pts_b
    assert len(pts_a) == 6
    assert pts_a[0] != pts_a[-1]


@pytest.mark.asyncio
async def test_websocket_client_handshake_and_send():
    key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")

    client = _WebSocketClient("ws://127.0.0.1:9222/devtools/page/1")
    client._reader = DummyReader(response)
    client._writer = DummyWriter()
    await client.connect(handshake_key=key)
    assert client._writer is not None


@pytest.mark.asyncio
async def test_engine_evaluate_and_dispatch(monkeypatch):
    engine = CDPTakeoverEngine()

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.responses = [
                json.dumps({"id": 1, "result": {"result": {"value": "https://example.com"}}}),
                json.dumps({"id": 2, "result": {}}),
            ]

        async def send_text(self, payload: str) -> None:
            self.sent.append(payload)

        async def recv_text(self) -> str:
            return self.responses.pop(0)

        async def close(self) -> None:
            return None

    engine._ws = FakeWS()  # type: ignore[assignment]
    assert await engine.get_current_url() == "https://example.com"
    await engine.dispatch_mouse_event("mouseMoved", 1, 2)
    assert "Input.dispatchMouseEvent" in engine._ws.sent[1]  # type: ignore[index]


def test_engine_connect_rejects_missing_targets(monkeypatch):
    engine = CDPTakeoverEngine()
    monkeypatch.setattr(
        "ticket_bot.browser.cdp_takeover._http_get_json",
        lambda _endpoint: [],
    )
    with pytest.raises(CDPError):
        asyncio.run(engine.connect("http://127.0.0.1:9222"))


def test_engine_connect_rejects_malformed_target_list(monkeypatch):
    engine = CDPTakeoverEngine()
    monkeypatch.setattr(
        "ticket_bot.browser.cdp_takeover._http_get_json",
        lambda _endpoint: {"not": "a-list"},
    )
    with pytest.raises(CDPError, match="malformed target list"):
        asyncio.run(engine.connect("http://127.0.0.1:9222"))
