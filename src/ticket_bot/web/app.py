"""Flask Web UI for the VieShow workflow."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ticket_bot.config import (
    AppConfig,
    EventConfig,
    SessionConfig,
    TakeoverConfig,
    VieShowConfig,
    load_config,
)
from ticket_bot.platforms.vieshow_parser import THEATER_CODES

logger = logging.getLogger(__name__)


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "takeover", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "legacy", "disabled"}:
        return False
    return default


def _is_local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _detect_chrome_executable() -> str:
    """Locate a Chrome/Chromium-compatible executable on Windows."""
    candidates = [
        os.getenv("BROWSER_EXECUTABLE_PATH", ""),
        os.getenv("CHROME_EXECUTABLE_PATH", ""),
        os.path.join(os.getenv("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.getenv("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.getenv("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.getenv("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.getenv("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.getenv("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""


def _chrome_user_data_dir(executable_path: str) -> Path:
    local_appdata = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
    exe_lower = executable_path.lower()
    if "msedge" in exe_lower:
        return local_appdata / "ticket-bot" / "edge-debug-profile"
    return local_appdata / "ticket-bot" / "chrome-debug-profile"


def _chrome_process_running() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return "chrome.exe" in result.stdout.lower()
    except Exception:
        return False


def _extract_takeover_settings(data: dict) -> dict:
    takeover_config = data.get("takeover_config")
    if not isinstance(takeover_config, dict):
        takeover_config = {}

    def pick(*keys: str, default: object = "") -> object:
        for source in (takeover_config, data):
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if value not in (None, ""):
                    return value
        return default

    mode = str(data.get("mode", "")).strip().lower()
    enabled = _parse_bool(
        pick("enabled", "takeover_enabled", "takeover", default=(mode == "takeover")),
        default=mode == "takeover",
    )
    debug_port = _parse_int(pick("debug_port", "attach_debug_port", "port", default=9222), 9222)
    cdp_url = str(pick("cdp_url", "attach_cdp_url", default="")).strip()
    if not cdp_url and enabled:
        cdp_url = f"http://127.0.0.1:{debug_port}"
    page_url_substring = str(
        pick("page_url_substring", "attach_page_url_substring", default="vscinemas.com.tw")
    ).strip() or "vscinemas.com.tw"

    return {
        "enabled": enabled,
        "mode": mode or ("takeover" if enabled else "run"),
        "debug_port": debug_port,
        "cdp_url": cdp_url,
        "page_url_substring": page_url_substring,
    }


def _apply_takeover_settings(cfg: AppConfig, takeover: dict) -> AppConfig:
    if not takeover.get("enabled"):
        return cfg

    cdp_url = str(takeover.get("cdp_url", "")).strip()
    page_url_substring = str(takeover.get("page_url_substring", "vscinemas.com.tw")).strip()

    cfg.browser.pre_warm = False
    cfg.browser.attach_cdp_url = cdp_url
    cfg.browser.attach_page_url_substring = page_url_substring
    cfg.browser.takeover_from_current_page = True

    cfg.vieshow.takeover = TakeoverConfig(
        enabled=True,
        cdp_url=cdp_url,
        debug_port=_parse_int(takeover.get("debug_port", 9222), 9222),
        page_url_substring=page_url_substring,
    )
    cfg.vieshow.takeover_mode = True
    cfg.vieshow.attach_cdp_url = cdp_url
    cfg.vieshow.attach_page_url_substring = page_url_substring
    return cfg


def _launch_handoff_browser(debug_port: int) -> tuple[bool, str]:
    if _is_local_port_open(debug_port):
        return True, f"接手 Chrome 已在 127.0.0.1:{debug_port} 等待附著"

    executable_path = _detect_chrome_executable()
    if not executable_path:
        return False, "找不到 Chrome/Edge 可執行檔，請先安裝瀏覽器或確認 PATH"

    user_data_dir = _chrome_user_data_dir(executable_path)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    command = [
        executable_path,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
    ]

    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.exception("Failed to launch handoff browser")
        return False, f"無法啟動接手 Chrome: {exc}"

    deadline = time.time() + 12.0
    while time.time() < deadline:
        if _is_local_port_open(debug_port):
            return True, (
                "接手 Chrome 已開啟。請先在 Chrome 中登入、過 Cloudflare，"
                "並手動選好影城、電影與場次，停在 booking.aspx 規定頁後再回來按「開始接手」。"
            )
        time.sleep(0.25)

    if _chrome_process_running():
        return False, (
            f"Chrome 已啟動，但 127.0.0.1:{debug_port} 沒有開啟偵錯埠。"
            "Chrome 可能忽略了偵錯旗標，或被本機安全軟體阻擋。請關閉剛開的接手視窗後再重試。"
        )

    return False, (
        f"Chrome 啟動後沒有在 127.0.0.1:{debug_port} 開啟偵錯埠。"
        "請確認 Chrome 可以正常啟動，然後重新嘗試。"
    )


def _fetch_takeover_showtimes(*, debug_port: int, page_url_substring: str) -> list[dict]:
    from ticket_bot.vieshow_takeover_v3 import read_showtime_options

    cdp_url = f"http://127.0.0.1:{debug_port}"
    return asyncio.run(read_showtime_options(cdp_url=cdp_url, page_url_substring=page_url_substring))


def create_app(config_path: str = "config.yaml") -> Flask:
    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.config["TICKET_BOT_CONFIG_PATH"] = config_path

    app.bot_state = {
        "running": False,
        "mode": "",
        "status": "idle",
        "logs": deque(maxlen=200),
    }
    app.bot_thread: threading.Thread | None = None
    app.bot_instance = None
    app.bot_loop: asyncio.AbstractEventLoop | None = None

    def _add_log(message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        app.bot_state["logs"].append(f"[{timestamp}] {message}")

    def _status_callback(message: str) -> None:
        _add_log(message)

    def _load_config() -> AppConfig:
        try:
            return load_config(app.config["TICKET_BOT_CONFIG_PATH"])
        except FileNotFoundError:
            logger.warning("Config file not found, using default AppConfig")
            return AppConfig()

    @app.route("/")
    def index():
        return render_template("index.html", theaters=THEATER_CODES)

    @app.route("/api/theaters")
    def api_theaters():
        return jsonify([{"code": code, "name": name} for code, name in THEATER_CODES.items()])

    @app.route("/api/status")
    def api_status():
        return jsonify(
            {
                "running": app.bot_state["running"],
                "mode": app.bot_state["mode"],
                "status": app.bot_state["status"],
                "logs": list(app.bot_state["logs"]),
            }
        )

    @app.route("/api/launch-handoff-browser", methods=["POST"])
    def api_launch_handoff_browser():
        data = request.get_json(silent=True) or {}
        debug_port = _parse_int(data.get("attach_debug_port", 9222), 9222)
        ok, message = _launch_handoff_browser(debug_port)
        if ok:
            return jsonify({"status": "ready" if _is_local_port_open(debug_port) else "launched", "message": message})
        return jsonify({"error": message}), 500

    @app.route("/api/takeover/showtimes", methods=["POST"])
    def api_takeover_showtimes():
        data = request.get_json(silent=True) or {}
        debug_port = _parse_int(data.get("debug_port", data.get("attach_debug_port", 9222)), 9222)
        page_url_substring = str(
            data.get("page_url_substring")
            or data.get("attach_page_url_substring")
            or data.get("page_filter")
            or "vscinemas.com.tw"
        ).strip() or "vscinemas.com.tw"

        if not _is_local_port_open(debug_port):
            return jsonify({"error": f"Chrome debugging port {debug_port} is not open"}), 400

        try:
            showtimes = _fetch_takeover_showtimes(
                debug_port=debug_port,
                page_url_substring=page_url_substring,
            )
        except Exception as exc:
            logger.exception("Failed to read takeover showtimes")
            return jsonify({"error": f"Failed to read showtimes: {exc}"}), 500

        return jsonify(
            {
                "debug_port": debug_port,
                "page_url_substring": page_url_substring,
                "showtimes": showtimes,
            }
        )

    @app.route("/api/start", methods=["POST"])
    def api_start():
        if app.bot_state["running"]:
            return jsonify({"error": "Bot is already running"}), 400

        data = request.get_json(silent=True) or {}
        cfg = _load_config()

        theater_code = str(data.get("theater_code", "")).strip()
        theater_keyword = str(data.get("theater_keyword", "")).strip()
        movie_keyword = str(data.get("movie_keyword", "")).strip()
        showtime_keyword = str(data.get("showtime_keyword", "")).strip()
        sale_time = str(data.get("sale_time", "")).strip()
        if not sale_time:
            sale_time_date = str(data.get("sale_time_date", "")).strip()
            sale_time_time = str(data.get("sale_time_time", "")).strip()
            if sale_time_date and sale_time_time:
                sale_time = f"{sale_time_date} {sale_time_time}:00"
        ticket_count = _parse_int(data.get("ticket_count", 2), 2)
        ticket_type = str(data.get("ticket_type", "full")).strip() or "full"
        seat_preference = str(data.get("seat_preference", "center")).strip() or "center"
        ishow_email = str(data.get("ishow_email", "")).strip()
        ishow_password = str(data.get("ishow_password", "")).strip()
        mode = str(data.get("mode", "run")).strip() or "run"
        watch_interval = _parse_float(data.get("watch_interval", 5.0), 5.0)
        takeover_config = data.get("takeover_config")
        if not isinstance(takeover_config, dict):
            takeover_config = {}
        selected_showtime_option_id = str(
            data.get("selected_showtime_option_id")
            or takeover_config.get("selected_showtime_option_id", "")
        ).strip()
        selected_showtime_value = str(
            data.get("selected_showtime_value")
            or takeover_config.get("selected_showtime_value", "")
        ).strip()

        takeover = _extract_takeover_settings(data)
        if takeover["enabled"] and not _is_local_port_open(takeover["debug_port"]):
            return jsonify(
                {
                    "error": (
                        f"接手 Chrome 尚未就緒，請先開啟 127.0.0.1:{takeover['debug_port']} 的接手瀏覽器"
                    )
                }
            ), 400

        cfg.vieshow = VieShowConfig(
            theater_code=theater_code,
            theater_keyword=theater_keyword,
            movie_keyword=movie_keyword,
            showtime_keyword=showtime_keyword,
            ticket_type=ticket_type,
            seat_preference=seat_preference,
            ishow_email=ishow_email,
            ishow_password=ishow_password,
            auto_login=bool(ishow_email and ishow_password),
            takeover=TakeoverConfig(
                enabled=takeover["enabled"],
                cdp_url=takeover["cdp_url"],
                debug_port=takeover["debug_port"],
                page_url_substring=takeover["page_url_substring"],
            ),
            takeover_mode=takeover["enabled"],
            attach_cdp_url=takeover["cdp_url"],
            attach_page_url_substring=takeover["page_url_substring"],
        )

        if takeover["enabled"]:
            _apply_takeover_settings(cfg, takeover)
        if selected_showtime_value:
            cfg.vieshow.showtime_keyword = selected_showtime_value

        event = EventConfig(
            name=movie_keyword or "VieShow Web UI",
            platform="vieshow",
            url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
            ticket_count=ticket_count,
            date_keyword=selected_showtime_value or showtime_keyword,
            sale_time=sale_time,
        )
        if selected_showtime_option_id:
            event.presale_code = selected_showtime_option_id

        session = SessionConfig(
            name="web-ui",
            user_data_dir=cfg.browser.user_data_dir,
        )

        app.bot_state["logs"].clear()
        app.bot_state["running"] = True
        app.bot_state["mode"] = "takeover" if takeover["enabled"] else mode
        app.bot_state["status"] = "running"

        _add_log(f"Starting mode: {app.bot_state['mode']}")
        if takeover["enabled"]:
            _add_log(f"Takeover port: {takeover['debug_port']}")
            _add_log(f"Takeover page filter: {takeover['page_url_substring']}")
        _add_log(f"Theater: {theater_code or theater_keyword or '(not set)'}")
        _add_log(f"Movie: {movie_keyword or '(not set)'}")
        _add_log(f"Showtime: {selected_showtime_value or showtime_keyword or '(not set)'}")
        _add_log(f"Sale time: {sale_time or '(not set)'}")
        _add_log(f"Tickets: {ticket_count}, type: {ticket_type}")
        _add_log(f"Seat preference: {seat_preference}")

        def _run_bot() -> None:
            loop = asyncio.new_event_loop()
            bot = None

            try:
                asyncio.set_event_loop(loop)
                app.bot_loop = loop

                from ticket_bot.platforms.vieshow import VieShowBot

                if takeover["enabled"]:
                    from ticket_bot.vieshow_takeover_v3 import VieShowTakeoverV3

                    bot = VieShowTakeoverV3(cfg, event, session=session)
                else:
                    bot = VieShowBot(cfg, event, session=session)
                bot.set_status_callback(_status_callback)
                app.bot_instance = bot

                if takeover["enabled"]:
                    success = loop.run_until_complete(bot.run())
                elif mode == "watch":
                    success = loop.run_until_complete(bot.watch(interval=watch_interval))
                else:
                    success = loop.run_until_complete(bot.run())

                if app.bot_state["status"] != "stopped":
                    app.bot_state["status"] = "success" if success else "error"
                    _add_log("Finished successfully" if success else "Finished without success")
            except Exception as exc:
                if app.bot_state["status"] != "stopped":
                    app.bot_state["status"] = "error"
                _add_log(f"[error] {type(exc).__name__}: {exc}")
                logger.exception("Web bot worker failed")
            finally:
                app.bot_state["running"] = False
                app.bot_instance = None
                app.bot_loop = None

                if bot is not None:
                    cleanup_loop = asyncio.new_event_loop()
                    try:
                        cleanup_loop.run_until_complete(bot.close())
                    except Exception:
                        logger.exception("Bot cleanup failed")
                    finally:
                        cleanup_loop.close()

                if not loop.is_closed():
                    loop.close()

        app.bot_thread = threading.Thread(target=_run_bot, daemon=True)
        app.bot_thread.start()

        return jsonify({"status": "started", "mode": "takeover" if takeover["enabled"] else mode})

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        if not app.bot_state["running"]:
            return jsonify({"error": "Bot is not running"}), 400

        app.bot_state["status"] = "stopped"
        _add_log("Stop requested")

        if app.bot_instance:
            try:
                app.bot_instance.request_stop()
            except Exception:
                logger.exception("Failed to request bot stop")

        return jsonify({"status": "stopping"})

    return app
