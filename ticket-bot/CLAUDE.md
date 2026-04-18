# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`ticket-bot` is a multi-platform ticket automation framework (v2.0.0) targeting Taiwan ticketing sites: **Tixcraft**, **KKTIX**, **VieShow**, and **Ticketmaster**. Documentation and comments are primarily in Traditional Chinese (zh-TW).

## Development Commands

```bash
# Install in editable mode
pip install -e .

# Install Playwright browser (required for playwright engine)
playwright install chromium

# Run all tests
pytest

# Run a specific test file
pytest tests/test_captcha.py

# Run tests verbosely
pytest -v

# Lint / format (ruff)
ruff check src/
ruff format src/

# Run the web UI (Flask, port 5000)
ticket-bot web

# Docker build
docker build -t ticket-bot .
```

## CLI Entry Points

All commands are available via `ticket-bot` (entrypoint: `ticket_bot.cli:cli`):

| Command | Purpose |
|---------|---------|
| `run` | Execute ticket grabbing for configured events |
| `watch` | Long-poll for ticket release |
| `login` | Open browser for manual platform login |
| `web` | Start Flask web UI on port 5000 |
| `bot -p telegram\|discord` | Launch notification bot listener |
| `list` | Display available events/showtimes |
| `monitor` | Ticketmaster API keyword monitoring |
| `countdown` | Sale-time countdown mode |
| `label` / `prepare` | Captcha training data annotation/preparation |

## Architecture

### Browser Engine Layer (`src/ticket_bot/browser/`)

Three engines share the same `BrowserEngine` / `PageWrapper` interface (defined in `base.py`):

1. **NoDriver** (`nodriver_engine.py`) — Preferred. Undetected Chrome with stealth JS injection (WebGL spoofing, navigator override).
2. **Playwright** (`playwright_engine.py`) — Fallback when NoDriver fails.
3. **CDP Takeover** (`cdp_takeover.py`) — Connects to the user's *already-running* Chrome via Chrome DevTools Protocol WebSocket. No Playwright/NoDriver needed; reuses existing cookies and history to avoid detection.

`factory.py` handles engine creation with automatic NoDriver→Playwright fallback.

### Platform Layer (`src/ticket_bot/platforms/`)

Each supported platform has:
- `{platform}.py` — Main `Bot` class with full end-to-end automation workflow
- `{platform}_parser.py` — HTML parsing utilities

VieShow additionally has versioned takeover implementations: `vieshow_takeover_v2/` and `vieshow_takeover_v3/`, and uses an explicit **state machine** (`theater_selection → ticket_type → quick_booking → seat_selection → checkout`) with JS-based state detection.

### Captcha Pipeline (`src/ticket_bot/captcha/`)

- Default solver uses **ddddocr** (public model). Users can swap in a custom ONNX model + `charset.json`.
- **Thompson Sampling Bandit** (`rl/bandit.py`) dynamically learns the optimal confidence threshold per execution environment using Beta distributions.
- **Collection mode** saves failed captchas for retraining.

### RL / Meta-Strategy Layer (`src/ticket_bot/rl/`)

Three independent learning modules run alongside ticket automation:
- `bandit.py` — Optimizes captcha confidence threshold
- `adaptive_retry.py` — Learns best retry intervals from historical data
- `burst_bandit.py` — Learns optimal click burst timing
- `gemma_advisor.py` — Local LLM (Ollama/Gemma) for context-aware strategy recommendations

### Notification Integrations

- **Telegram** (`telegram_bot.py`, 76KB) — Long-polling bot with NLU, tixcraft search, Gemma AI advisor fallback
- **Discord** (`discord_bot.py`) — Owner-only slash commands

### Web UI (`src/ticket_bot/web/`)

Flask app (`app.py`) with a vanilla JS dark-themed front-end (`templates/index.html`). Supports two modes:
- **Takeover mode** — User selects showtime in their Chrome; bot connects via CDP and completes checkout
- **Legacy mode** — Traditional headless run/watch flow

Real-time log streaming uses a `deque` buffer.

## Configuration

The bot is configured via `config.yaml` (copy from `config.yaml.example`) plus `.env` (copy from `.env.example`). Config is loaded into typed Python dataclasses in `src/ticket_bot/config.py` (`AppConfig` root). Multi-session parallelism is supported — each session gets its own `user_data_dir` and optional proxy.

Key config sections: `events`, `browser`, `captcha`, `notifications`, `proxy`, `trace`, `vieshow`, `gemma`, `sessions`.

## Testing

Tests use **pytest** + **pytest-asyncio**. `tests/conftest.py` provides an `httpx` mock fixture for intercepting async HTTP requests. Test files map closely to source modules (e.g., `test_captcha.py`, `test_vieshow.py`, `test_tixcraft.py`).

## Python Version

Requires Python >= 3.11. **Python 3.14 is incompatible** with NoDriver — use 3.11–3.12.
