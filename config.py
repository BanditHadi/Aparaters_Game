"""
Country Boost Live -> Streamer Boost Live - configuration.

Default chat source is Aparat.  YouTube is still available with
`--source youtube` or `SOURCE=youtube` in .env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
AUDIO_DIR = CACHE_DIR / "audio"
FLAG_DIR = CACHE_DIR / "flags"
PICS_DIR = DATA_DIR / "pics"
STATE_FILE = DATA_DIR / "state.json"
STREAMERS_FILE = DATA_DIR / "streamers.json"
BROWSER_DIR = DATA_DIR / "browser"

APARAT_PROFILE_API = "https://www.aparat.com/etc/api/profile/username/{username}"


def load_dotenv(path: Path | None = None) -> None:
    path = path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        if cast is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return cast(raw.strip())
    except ValueError:
        return default


USD_RATES = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.65, "NZD": 0.60,
    "JPY": 0.0067, "KRW": 0.00073, "CNY": 0.14, "HKD": 0.128, "TWD": 0.031,
    "INR": 0.012, "PKR": 0.0036, "BDT": 0.0084, "LKR": 0.0033, "NPR": 0.0075,
    "IDR": 0.000062, "MYR": 0.21, "SGD": 0.74, "THB": 0.028, "VND": 0.00004,
    "PHP": 0.017, "TRY": 0.029, "RUB": 0.011, "UAH": 0.024, "PLN": 0.25,
    "CZK": 0.043, "HUF": 0.0027, "RON": 0.22, "BGN": 0.55, "SEK": 0.095,
    "NOK": 0.093, "DKK": 0.145, "CHF": 1.12, "ILS": 0.27, "SAR": 0.267,
    "AED": 0.272, "QAR": 0.275, "KWD": 3.25, "BHD": 2.65, "OMR": 2.6,
    "JOD": 1.41, "EGP": 0.02, "MAD": 0.10, "DZD": 0.0074, "TND": 0.32,
    "NGN": 0.00065, "KES": 0.0077, "ZAR": 0.054, "BRL": 0.18, "ARS": 0.001,
    "CLP": 0.00105, "COP": 0.00025, "PEN": 0.27, "MXN": 0.055, "IRR": 0.0000024,
    "IQD": 0.00076, "KZT": 0.002, "AZN": 0.59, "GEL": 0.37, "AMD": 0.0026,
    "UZS": 0.00008,
}


@dataclass
class Settings:
    # ---------------------------------------------------------------- source
    source: str = "aparat"
    video: str = ""
    backend: str = "pytchat"
    api_key: str = ""
    api_poll_seconds: float = 10.0
    demo: bool = False
    demo_rate: float = 4.0

    # ---------------------------------------------------------------- aparat
    aparat_channel_url: str = ""
    aparat_mode: str = "dom"
    aparat_headless: bool = True
    aparat_warmup_seconds: int = 5
    aparat_message_selector: str = ".message, .ruby-message"
    aparat_user_selector: str = ".message__username, a.channel"
    aparat_text_selector: str = ".message__text, .details-box > .ruby-message"
    aparat_ws_url_filter: str = ""
    aparat_debug: bool = False

    # ---------------------------------------------------------------- ruby
    ruby_points: int = 2
    ruby_min_points: int = 2

    # ---------------------------------------------------------------- booster
    booster_target: int = 50000          # every N total points -> booster
    booster_duration: float = 60.0       # seconds
    booster_multiplier: int = 2          # points multiplier during booster

    # ---------------------------------------------------------------- server
    host: str = "0.0.0.0"
    port: int = 8080
    admin_password: str = "admin"

    # --------------------------------------------------------------- scoring
    level_thresholds: tuple = (0, 25, 50, 100, 200, 500, 800, 1100, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000)
    max_streamers_per_message: int = 1
    user_cooldown: float = 0.0
    member_points: int = 400
    pending_minutes: int = 15

    # ------------------------------------------------------ game announcements
    fire_points: int = 50
    fire_window: float = 10.0
    fire_cooldown: float = 90.0
    announce_top_n: int = 10
    milestones: tuple = (100, 500, 1000, 2500, 5000, 10000, 25000, 50000,
                         100000, 250000, 500000, 1000000)
    reminder_minutes: float = 7.0
    standings_minutes: float = 5.0

    # ----------------------------------------------------------------- voice
    tts_engine: str = "edge"
    tts_voice: str = "en-US-AndrewMultilingualNeural"
    tts_voice_fa: str = "fa-IR-FaridNeural"
    tts_rate: str = "+8%"
    tts_pitch: str = "+0Hz"
    tts_volume: str = "+0%"
    browser_tts_fallback: bool = True
    non_latin_names: str = "skip"
    language: str = "fa"

    # -------------------------------------------------------------------- ui
    title: str = "BOOST YOUR STREAMER"

    @property
    def max_level(self) -> int:
        return len(self.level_thresholds)

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        d.source = _env("SOURCE", d.source).lower()
        d.video = _env("YT_VIDEO", d.video)
        d.backend = _env("YT_BACKEND", d.backend).lower()
        d.api_key = _env("YT_API_KEY", d.api_key)
        d.api_poll_seconds = _env("YT_API_POLL", d.api_poll_seconds, float)
        d.demo = _env("DEMO", d.demo, bool)
        d.demo_rate = _env("DEMO_RATE", d.demo_rate, float)
        d.aparat_channel_url = _env("APARAT_URL", d.aparat_channel_url)
        d.aparat_mode = _env("APARAT_MODE", d.aparat_mode).lower()
        d.aparat_headless = _env("APARAT_HEADLESS", d.aparat_headless, bool)
        d.aparat_warmup_seconds = _env("APARAT_WARMUP", d.aparat_warmup_seconds, int)
        d.aparat_message_selector = _env("APARAT_MSG_SEL", d.aparat_message_selector)
        d.aparat_user_selector = _env("APARAT_USER_SEL", d.aparat_user_selector)
        d.aparat_text_selector = _env("APARAT_TEXT_SEL", d.aparat_text_selector)
        d.aparat_ws_url_filter = _env("APARAT_WS_FILTER", d.aparat_ws_url_filter)
        d.aparat_debug = _env("APARAT_DEBUG", d.aparat_debug, bool)
        d.ruby_points = _env("RUBY_POINTS", d.ruby_points, int)
        d.ruby_min_points = _env("RUBY_MIN_POINTS", d.ruby_min_points, int)
        d.booster_target = _env("BOOSTER_TARGET", d.booster_target, int)
        d.booster_duration = _env("BOOSTER_DURATION", d.booster_duration, float)
        d.booster_multiplier = _env("BOOSTER_MULTIPLIER", d.booster_multiplier, int)
        d.host = _env("HOST", d.host)
        d.port = _env("PORT", d.port, int)
        d.admin_password = _env("ADMIN_PASSWORD", d.admin_password)
        d.user_cooldown = _env("USER_COOLDOWN", d.user_cooldown, float)
        d.max_streamers_per_message = _env("MAX_STREAMERS", d.max_streamers_per_message, int)
        d.member_points = _env("MEMBER_POINTS", d.member_points, int)
        d.tts_engine = _env("TTS_ENGINE", d.tts_engine).lower()
        d.tts_voice = _env("TTS_VOICE", d.tts_voice)
        d.tts_voice_fa = _env("TTS_VOICE_FA", d.tts_voice_fa)
        d.tts_rate = _env("TTS_RATE", d.tts_rate)
        d.tts_pitch = _env("TTS_PITCH", d.tts_pitch)
        d.non_latin_names = _env("NON_LATIN_NAMES", d.non_latin_names).lower()
        d.language = _env("LANGUAGE", d.language).lower()
        d.reminder_minutes = _env("REMINDER_MINUTES", d.reminder_minutes, float)
        d.standings_minutes = _env("STANDINGS_MINUTES", d.standings_minutes, float)
        d.title = _env("TITLE", d.title)
        if d.demo:
            d.source = "demo"
        return d