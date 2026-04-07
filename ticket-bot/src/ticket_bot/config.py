"""設定載入：YAML + .env"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from ticket_bot.gemma_client import GemmaConfig


@dataclass
class EventConfig:
    name: str
    platform: str
    url: str
    ticket_count: int = 2
    date_keyword: str = ""
    area_keyword: str = ""
    sale_time: str = ""
    presale_code: str = ""


@dataclass
class BrowserConfig:
    engine: str = "nodriver"
    headless: bool = False
    user_data_dir: str = "./chrome_profile"
    pre_warm: bool = True
    lang: str = "zh-TW"
    executable_path: str = ""  # 留空讓 nodriver/playwright 自動偵測 Chrome 路徑
    api_mode: str = "off"  # "off" | "checkout" | "full"
    turbo_mode: bool = True  # 極速模式：JS 填充與送出，不模擬人類打字行為
    attach_cdp_url: str = ""
    attach_page_url_substring: str = ""
    takeover_from_current_page: bool = False


@dataclass
class TakeoverConfig:
    """VieShow 接管模式設定。"""

    enabled: bool = False
    cdp_url: str = ""
    debug_port: int = 9222
    page_url_substring: str = "vscinemas.com.tw"

    def resolved_cdp_url(self) -> str:
        if self.cdp_url:
            return self.cdp_url
        return f"http://127.0.0.1:{self.debug_port}"


@dataclass
class CaptchaConfig:
    engine: str = "ddddocr"
    beta_model: bool = True
    char_ranges: int = 1
    confidence_threshold: float = 0.6
    max_attempts: int = 5
    preprocess: bool = True
    custom_model_path: str = ""     # 自訓練 ONNX 模型路徑
    custom_charset_path: str = ""   # 自訓練模型字元集路徑
    collect_dir: str = ""           # 驗證碼收集目錄（訓練用）


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""


@dataclass
class NotificationConfig:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)


@dataclass
class ProxyConfig:
    enabled: bool = False
    rotate: bool = True
    servers: list[str] = field(default_factory=list)


@dataclass
class TraceConfig:
    enabled: bool = False
    log_path: str = "./logs/tixcraft_trace.jsonl"


@dataclass
class KKTIXAutofillConfig:
    enabled: bool = False
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    contact_gender: str = ""
    contact_birth_date: str = ""
    contact_region: str = ""
    attendee_names: list[str] = field(default_factory=list)
    attendee_phones: list[str] = field(default_factory=list)
    attendee_id_numbers: list[str] = field(default_factory=list)
    agree_real_name: bool = True
    display_public_attendance: bool = False
    join_organizer_fan: bool = False


@dataclass
class VieShowConfig:
    """威秀影城搶票設定"""
    theater_code: str = ""           # 影城代碼：TP, MU, NL...
    theater_keyword: str = ""        # 或用影城名稱關鍵字搜尋
    movie_keyword: str = ""          # 電影名稱關鍵字
    showtime_keyword: str = ""       # 場次關鍵字（如 IMAX, 19:30）
    ticket_type: str = "full"        # full / student / ishow
    seat_preference: str = "center"  # center / front / back 或指定座位如 "F12,F13"
    ishow_email: str = ""
    ishow_password: str = ""
    auto_login: bool = True
    takeover: TakeoverConfig = field(default_factory=TakeoverConfig)
    takeover_mode: bool = False
    attach_cdp_url: str = ""
    attach_page_url_substring: str = "vscinemas.com.tw"


@dataclass
class DeploymentConfig:
    profile: str = ""



@dataclass
class SessionConfig:
    """單一搶票 session（多帳號並行用）"""
    name: str = "default"
    user_data_dir: str = "./chrome_profile"
    proxy_server: str = ""
    cookie_file: str = ""


@dataclass
class AppConfig:
    events: list[EventConfig] = field(default_factory=list)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    kktix: KKTIXAutofillConfig = field(default_factory=KKTIXAutofillConfig)
    vieshow: VieShowConfig = field(default_factory=VieShowConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    sessions: list[SessionConfig] = field(default_factory=list)
    gemma: GemmaConfig = field(default_factory=GemmaConfig)
    ticketmaster_api_key: str = ""


DEPLOYMENT_PROFILE_ALIASES = {
    "local": "local_desktop",
    "local_desktop": "local_desktop",
    "gcp": "gcp_taiwan",
    "cloud": "gcp_taiwan",
    "gcp_taiwan": "gcp_taiwan",
    "aws": "aws_tokyo",
    "tokyo": "aws_tokyo",
    "aws_tokyo": "aws_tokyo",
    "aws-tokyo": "aws_tokyo",
}


DEPLOYMENT_PROFILE_PRESETS = {
    "local_desktop": {
        "browser": {
            "engine": "nodriver",
            "headless": False,
            "user_data_dir": "./chrome_profile",
            "pre_warm": True,
            "lang": "zh-TW",
            "executable_path": "",
            "api_mode": "full",
            "turbo_mode": True,
        },
        "captcha": {
            "engine": "ddddocr",
            "beta_model": True,
            "char_ranges": 0,
            "confidence_threshold": 0.6,
            "max_attempts": 5,
            "preprocess": False,
            "custom_model_path": "model/captcha_model.onnx",
            "custom_charset_path": "model/charset.json",
            "collect_dir": "./captcha_samples",
        },
        "notifications": {
            "telegram": {"enabled": True},
            "discord": {"enabled": True},
        },
        "proxy": {
            "enabled": False,
            "rotate": True,
            "servers": [],
        },
        "trace": {
            "enabled": True,
            "log_path": "./logs/tixcraft_trace_local.jsonl",
        },
    },
    "gcp_taiwan": {
        "browser": {
            "engine": "playwright",
            "headless": True,
            "user_data_dir": "./chrome_profile_node_1",
            "pre_warm": True,
            "lang": "zh-TW",
            "executable_path": "/usr/bin/chromium",
            "api_mode": "full",
            "turbo_mode": True,
        },
        "captcha": {
            "engine": "ddddocr",
            "beta_model": True,
            "char_ranges": 0,
            "confidence_threshold": 0.6,
            "max_attempts": 5,
            "preprocess": False,
            "custom_model_path": "model/captcha_model.onnx",
            "custom_charset_path": "model/charset.json",
            "collect_dir": "",
        },
        "notifications": {
            "telegram": {"enabled": True},
            "discord": {"enabled": True},
        },
        "proxy": {
            "enabled": False,
            "rotate": True,
            "servers": [],
        },
        "trace": {
            "enabled": True,
            "log_path": "./logs/tixcraft_trace_cloud.jsonl",
        },
    },
    "aws_tokyo": {
        "browser": {
            "engine": "playwright",
            "headless": True,
            "user_data_dir": "./chrome_profile_node_1",
            "pre_warm": True,
            "lang": "zh-TW",
            "executable_path": "/usr/bin/chromium",
            "api_mode": "full",
            "turbo_mode": True,
        },
        "captcha": {
            "engine": "ddddocr",
            "beta_model": True,
            "char_ranges": 0,
            "confidence_threshold": 0.6,
            "max_attempts": 5,
            "preprocess": False,
            "custom_model_path": "model/captcha_model.onnx",
            "custom_charset_path": "model/charset.json",
            "collect_dir": "",
        },
        "notifications": {
            "telegram": {"enabled": False},
            "discord": {"enabled": False},
        },
        "proxy": {
            "enabled": False,
            "rotate": True,
            "servers": [],
        },
        "trace": {
            "enabled": True,
            "log_path": "./logs/tixcraft_trace_aws_tokyo.jsonl",
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_deployment_profile(profile: str) -> str:
    key = profile.strip().lower().replace("-", "_")
    return DEPLOYMENT_PROFILE_ALIASES.get(key, key)


def _parse_env_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """載入 YAML 設定檔 + .env 環境變數"""
    load_dotenv(env_path, override=True)

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"找不到設定檔：{config_path}")

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    deployment_raw = raw.get("deployment", {}).copy()
    deployment_profile = _normalize_deployment_profile(
        os.getenv("DEPLOYMENT_PROFILE", deployment_raw.get("profile", ""))
    )
    deployment = DeploymentConfig(profile=deployment_profile)
    profile_preset = DEPLOYMENT_PROFILE_PRESETS.get(deployment_profile, {})

    # 解析 events
    events = [EventConfig(**e) for e in raw.get("events", [])]

    # 解析 browser（支援環境變數覆蓋，方便雲端部署）
    browser_raw = _deep_merge(profile_preset.get("browser", {}), raw.get("browser", {}))
    
    # 支援分散式擴展：根據 NODE_ID 建立獨立的 profile
    node_id = os.getenv("NODE_ID", "")
    if node_id:
        # 如果是分散式節點，強制建立獨立目錄避免 lock 衝突
        browser_raw["user_data_dir"] = f"./chrome_profile_node_{node_id}"

    if os.getenv("BROWSER_ENGINE"):
        browser_raw["engine"] = os.getenv("BROWSER_ENGINE")
    if os.getenv("BROWSER_HEADLESS"):
        browser_raw["headless"] = os.getenv("BROWSER_HEADLESS", "").lower() == "true"
    if os.getenv("BROWSER_EXECUTABLE_PATH"):
        browser_raw["executable_path"] = os.getenv("BROWSER_EXECUTABLE_PATH")
    if os.getenv("BROWSER_API_MODE"):
        browser_raw["api_mode"] = os.getenv("BROWSER_API_MODE")
    browser = BrowserConfig(**browser_raw)

    # 解析 captcha（支援環境變數覆蓋，方便雲端訓練/收集）
    captcha_raw = _deep_merge(profile_preset.get("captcha", {}), raw.get("captcha", {}))
    if os.getenv("CAPTCHA_COLLECT_DIR"):
        captcha_raw["collect_dir"] = os.getenv("CAPTCHA_COLLECT_DIR")
    elif os.getenv("CAPTCHA_COLLECT_ENABLED", "").lower() == "true" and not captcha_raw.get("collect_dir"):
        captcha_raw["collect_dir"] = "./captcha_samples"
    captcha = CaptchaConfig(**captcha_raw)

    # 解析 KKTIX autofill（先走 YAML，避免把個資硬編碼進程式）
    kktix_raw = raw.get("kktix", {})
    kktix = KKTIXAutofillConfig(**kktix_raw)

    # 解析 VieShow 威秀影城設定
    vieshow_raw = raw.get("vieshow", {}).copy()
    if os.getenv("VIESHOW_ISHOW_EMAIL"):
        vieshow_raw["ishow_email"] = os.getenv("VIESHOW_ISHOW_EMAIL")
    if os.getenv("VIESHOW_ISHOW_PASSWORD"):
        vieshow_raw["ishow_password"] = os.getenv("VIESHOW_ISHOW_PASSWORD")
    takeover_raw = vieshow_raw.pop("takeover", {}) or {}
    legacy_takeover_enabled = bool(
        takeover_raw.get("enabled")
        or vieshow_raw.get("takeover_mode")
        or browser.takeover_from_current_page
        or browser.attach_cdp_url
    )
    if "enabled" not in takeover_raw:
        takeover_raw["enabled"] = legacy_takeover_enabled
    if not takeover_raw.get("cdp_url"):
        takeover_raw["cdp_url"] = (
            vieshow_raw.get("attach_cdp_url")
            or browser.attach_cdp_url
            or ""
        )
    if not takeover_raw.get("page_url_substring"):
        takeover_raw["page_url_substring"] = (
            vieshow_raw.get("attach_page_url_substring")
            or browser.attach_page_url_substring
            or "vscinemas.com.tw"
        )
    takeover = TakeoverConfig(**takeover_raw)
    vieshow_raw["takeover"] = takeover
    vieshow_raw["takeover_mode"] = bool(vieshow_raw.get("takeover_mode") or takeover.enabled)
    vieshow_raw["attach_cdp_url"] = vieshow_raw.get("attach_cdp_url") or takeover.resolved_cdp_url()
    vieshow_raw["attach_page_url_substring"] = (
        vieshow_raw.get("attach_page_url_substring")
        or takeover.page_url_substring
    )
    vieshow = VieShowConfig(**vieshow_raw)

    # 解析 notifications（合併 .env 機密）
    notif_raw = _deep_merge(profile_preset.get("notifications", {}), raw.get("notifications", {}))
    tg_raw = notif_raw.get("telegram", {})
    telegram = TelegramConfig(
        enabled=tg_raw.get("enabled", False),
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=tg_raw.get("chat_id", "") or os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    dc_raw = notif_raw.get("discord", {})
    discord = DiscordConfig(
        enabled=dc_raw.get("enabled", False),
        webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
    )
    notifications = NotificationConfig(telegram=telegram, discord=discord)

    # 解析 proxy
    proxy_raw = _deep_merge(profile_preset.get("proxy", {}), raw.get("proxy", {}))
    if os.getenv("PROXY_ENABLED"):
        proxy_raw["enabled"] = os.getenv("PROXY_ENABLED", "").lower() == "true"
    if os.getenv("PROXY_ROTATE"):
        proxy_raw["rotate"] = os.getenv("PROXY_ROTATE", "").lower() == "true"
    if os.getenv("PROXY_SERVERS"):
        proxy_raw["servers"] = _parse_env_list(os.getenv("PROXY_SERVERS", ""))
    proxy = ProxyConfig(
        enabled=proxy_raw.get("enabled", False),
        rotate=proxy_raw.get("rotate", True),
        servers=proxy_raw.get("servers", []),
    )

    # 解析 trace（可用於記錄 Cloudflare / redirect / session affinity）
    trace_raw = _deep_merge(profile_preset.get("trace", {}), raw.get("trace", {}))
    if os.getenv("TIXCRAFT_TRACE_HEADERS"):
        trace_raw["enabled"] = os.getenv("TIXCRAFT_TRACE_HEADERS", "").lower() == "true"
    if os.getenv("TIXCRAFT_TRACE_LOG_PATH"):
        trace_raw["log_path"] = os.getenv("TIXCRAFT_TRACE_LOG_PATH")
    trace = TraceConfig(**trace_raw)

    # 解析 sessions（多帳號並行）
    sessions_raw = raw.get("sessions", [])
    sessions = [SessionConfig(**s) for s in sessions_raw]
    # 如果沒有設定 sessions，用 browser.user_data_dir 建一個預設 session
    if not sessions:
        sessions = [SessionConfig(name="default", user_data_dir=browser.user_data_dir)]

    # 解析 Gemma 4 設定
    gemma_raw = raw.get("gemma", {})
    if os.getenv("GEMMA_ENABLED"):
        gemma_raw["enabled"] = os.getenv("GEMMA_ENABLED", "").lower() == "true"
    if os.getenv("GEMMA_MODEL"):
        gemma_raw["model"] = os.getenv("GEMMA_MODEL")
    if os.getenv("GEMMA_OLLAMA_URL"):
        gemma_raw["ollama_url"] = os.getenv("GEMMA_OLLAMA_URL")
    if os.getenv("GEMMA_API_KEY"):
        gemma_raw["api_key"] = os.getenv("GEMMA_API_KEY")
    gemma = GemmaConfig(**gemma_raw)

    return AppConfig(
        events=events,
        deployment=deployment,
        browser=browser,
        captcha=captcha,
        kktix=kktix,
        vieshow=vieshow,
        notifications=notifications,
        proxy=proxy,
        trace=trace,
        sessions=sessions,
        gemma=gemma,
        ticketmaster_api_key=os.getenv("TICKETMASTER_API_KEY", ""),
    )
