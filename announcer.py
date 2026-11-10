"""
Voice announcer ("the commentator").

The game engine calls `announcer.say("Bandit passes Sara ...", kind=..., priority=...)`.
The announcer
  1. rate-limits every kind of announcement (no spam),
  2. keeps a priority queue (Rubies beat overtakes, overtakes beat chit-chat),
  3. turns the text into a speech clip (edge-tts / pyttsx3),
  4. tells the overlay to play it (and to show the subtitle on screen).

The announcer always speaks Persian (fa-IR) by default.  If a line contains
English/Latin words they are read by the Persian voice with its best effort.

Engines:  "edge"     Microsoft neural voices through `edge-tts` (best quality, needs internet)
          "pyttsx3"  offline system voice
          "browser"  the overlay speaks with the browser's own voice
          "off"      no voice (subtitles only)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import unicodedata
import wave
from dataclasses import dataclass, field
from pathlib import Path

from config import AUDIO_DIR, Settings

log = logging.getLogger("announcer")

# --------------------------------------------------------------------------- #
# Persian number + ordinal helpers
# --------------------------------------------------------------------------- #
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_FA_ORDINALS = {
    1: "اول", 2: "دوم", 3: "سوم", 4: "چهارم", 5: "پنجم",
    6: "ششم", 7: "هفتم", 8: "هشتم", 9: "نهم", 10: "دهم",
    11: "یازدهم", 12: "دوازدهم", 13: "سیزدهم", 14: "چهاردهم", 15: "پانزدهم",
    16: "شانزدهم", 17: "هفدهم", 18: "هجدهم", 19: "نوزدهم", 20: "بیستم",
}


def ordinal(n: int) -> str:
    """Persian ordinal for the given number ('اول', 'دوم', ...)."""
    n = int(n)
    if n in _FA_ORDINALS:
        return _FA_ORDINALS[n]
    # fall back to "بیست و یکم" style for 21..99
    if n < 100:
        tens = (n // 10) * 10
        ones = n % 10
        tens_word = {
            20: "بیستم", 30: "سی‌ام", 40: "چهلم", 50: "پنجاهم",
            60: "شصتم", 70: "هفتادم", 80: "هشتادم", 90: "نودم",
        }.get(tens, "")
        ones_word = _FA_ORDINALS.get(ones, "")
        if tens_word and ones_word:
            return tens_word.replace("م", "") + " و " + ones_word
        return tens_word or ones_word or str(n)
    return str(n)


def _trim(x: float) -> str:
    s = f"{x:.1f}"
    return s[:-2] if s.endswith(".0") else s


# --------------------------------------------------------------------------- #
# Persian spoken numbers
# --------------------------------------------------------------------------- #
_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده",
          "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد",
             "پانصد", "شصد", "هفتصد", "هشتصد", "نهصد"]


def _fa_under_1000(n: int) -> str:
    parts = []
    h = n // 100
    r = n % 100
    if h:
        parts.append(_HUNDREDS[h])
    if r:
        if r < 10:
            parts.append(_ONES[r])
        elif r < 20:
            parts.append(_TEENS[r - 10])
        else:
            t = r // 10
            o = r % 10
            if o:
                parts.append(_TENS[t] + " و " + _ONES[o])
            else:
                parts.append(_TENS[t])
    return " و ".join(parts)


def _fa_integer(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 0:
        return "منفی " + _fa_integer(-n)
    parts = []
    for div, name in (
        (10 ** 18, "تریلیارد"),
        (10 ** 15, "بیلیارد"),
        (10 ** 12, "تریلیون"),
        (10 ** 9, "میلیارد"),
        (10 ** 6, "میلیون"),
        (10 ** 3, "هزار"),
    ):
        if n >= div:
            q = n // div
            r = n % div
            parts.append(_fa_under_1000(q) + " " + name)
            n = r
    if n:
        parts.append(_fa_under_1000(n))
    return " و ".join(parts)


def spoken_points(n) -> str:
    """Persian spoken form of a score - handles huge values too."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n < 0:
        return "منفی " + spoken_points(-n)
    if n >= 10 ** 21:
        # very large: scientific in Persian
        s = str(n)
        exp = len(s) - 1
        return f"{s[0]} ضربدر ده به توان {exp}"
    return _fa_integer(n)


def spoken_rubies(n) -> str:
    n = int(n)
    word = "روبی"  # same singular / plural in Persian
    return f"{spoken_points(n)} {word}"


def to_fa_digits(s) -> str:
    return str(s).translate(_FA_DIGITS)


_LETTER = re.compile(r"[A-Za-z]")


def speakable_name(name: str, settings: Settings | None = None) -> str:
    """Keep the display name as-is for Persian voice - it will read both scripts."""
    name = unicodedata.normalize("NFKC", name or "").lstrip("@")
    name = "".join(ch if (ch.isalnum() or ch in " ._-\u200c") else " " for ch in name)
    name = re.sub(r"[._\-]+", " ", name)
    name = re.sub(r"\d{3,}", "", name)
    name = " ".join(name.split())[:30]
    return name or "یک بیننده"


def estimate_duration(text: str) -> float:
    return max(1.8, len(text) / 13.5 + 0.8)


# --------------------------------------------------------------------------- #
# language detection (used only if a settings.language switch is added later)
# --------------------------------------------------------------------------- #
_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def looks_persian(text: str) -> bool:
    for ch in text or "":
        cp = ord(ch)
        for lo, hi in _ARABIC_RANGES:
            if lo <= cp <= hi:
                return True
    return False


# --------------------------------------------------------------------------- #
# speech backends
# --------------------------------------------------------------------------- #
class EdgeBackend:
    def __init__(self, s: Settings):
        import edge_tts  # noqa: F401
        self.s = s

    def _voice_for(self, text: str) -> str:
        # Always prefer the Persian voice; fall back to English only if
        # explicitly configured differently.
        fa = getattr(self.s, "tts_voice_fa", "fa-IR-FaridNeural")
        if fa:
            return fa
        return self.s.tts_voice

    async def synth(self, text: str):
        import edge_tts
        voice = self._voice_for(text)
        fallbacks = [voice, "fa-IR-FaridNeural", "fa-IR-DilaraNeural"]

        last: Exception | None = None
        for v in dict.fromkeys(fallbacks):
            key = hashlib.sha1(
                f"{v}|{self.s.tts_rate}|{self.s.tts_pitch}|{text}".encode()).hexdigest()[:24]
            path = AUDIO_DIR / f"{key}.mp3"
            if path.exists() and path.stat().st_size > 800:
                return path, path.stat().st_size / 6000 + 0.2
            try:
                comm = edge_tts.Communicate(text, v, rate=self.s.tts_rate,
                                            volume=self.s.tts_volume, pitch=self.s.tts_pitch)
                await asyncio.wait_for(comm.save(str(path)), timeout=25)
                if path.exists() and path.stat().st_size > 800:
                    return path, path.stat().st_size / 6000 + 0.2
            except Exception as exc:
                last = exc
                path.unlink(missing_ok=True)
        raise RuntimeError(f"edge-tts failed: {last}")


class Pyttsx3Backend:
    def __init__(self, s: Settings):
        import pyttsx3  # noqa: F401
        self.s = s

    def _blocking(self, text: str, path: Path) -> None:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        # try to pick a Persian voice if available
        for v in engine.getProperty("voices") or []:
            langs = (getattr(v, "languages", None) or [])
            for l in langs:
                try:
                    s = l.decode("utf-8", "ignore") if isinstance(l, bytes) else str(l)
                except Exception:
                    s = str(l)
                if "fa" in s.lower() or "persian" in s.lower():
                    engine.setProperty("voice", v.id)
                    break
        engine.save_to_file(text, str(path))
        engine.runAndWait()
        engine.stop()

    async def synth(self, text: str):
        key = hashlib.sha1(f"pyttsx3-fa|{text}".encode()).hexdigest()[:24]
        path = AUDIO_DIR / f"{key}.wav"
        if not path.exists() or path.stat().st_size < 800:
            await asyncio.to_thread(self._blocking, text, path)
        try:
            with wave.open(str(path), "rb") as w:
                duration = w.getnframes() / float(w.getframerate())
        except Exception:
            duration = estimate_duration(text)
        return path, duration + 0.2


# --------------------------------------------------------------------------- #
# the announcer
# --------------------------------------------------------------------------- #
@dataclass(order=True)
class _Item:
    priority: int
    order: int
    text: str = field(compare=False)
    kind: str = field(compare=False, default="info")
    created: float = field(compare=False, default=0.0)
    ttl: float = field(compare=False, default=20.0)


GAPS = {"overtake": 6, "leader": 3, "fire": 8, "milestone": 4, "join": 12,
        "chatter": 15, "standings": 0, "reminder": 0, "ruby": 0, "superchat": 0,
        "member": 0, "achievement": 4, "test": 0, "admin": 0}
MAX_QUEUE = 5


class Announcer:
    def __init__(self, settings: Settings, emit, has_clients):
        self.s = settings
        self.emit = emit
        self.has_clients = has_clients
        self.muted = False
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._counter = 0
        self._last: dict[str, float] = {}
        self._backend = None
        self._browser = False

    # ---------------------------------------------------------------- public
    def say(self, text: str, kind: str = "info", priority: int = 5, ttl: float = 20.0) -> None:
        now = time.monotonic()
        if now - self._last.get(kind, -1e9) < GAPS.get(kind, 0):
            return
        if self.queue.qsize() >= MAX_QUEUE and priority >= 4:
            return
        self._last[kind] = now
        self._counter += 1
        self.queue.put_nowait(_Item(priority, self._counter, text, kind, now, ttl))

    # -------------------------------------------------------------- admin
    def clear_queue(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def set_voices(self, voice_en: str | None = None, voice_fa: str | None = None) -> None:
        if voice_en:
            self.s.tts_voice = voice_en
        if voice_fa:
            self.s.tts_voice_fa = voice_fa
        self._backend = None
        self._browser = False
        self._make_backend()

    def test_voice(self, text: str = "این یک تست صدای گوینده است.") -> None:
        self._last.pop("test", None)
        self.say(text, kind="test", priority=0, ttl=60)

    # ------------------------------------------------------------------- run
    def _make_backend(self) -> None:
        engine = self.s.tts_engine
        try:
            if engine == "edge":
                self._backend = EdgeBackend(self.s)
            elif engine == "pyttsx3":
                self._backend = Pyttsx3Backend(self.s)
            elif engine == "browser":
                self._browser = True
            elif engine != "off":
                log.warning("Unknown TTS engine %r - voice disabled", engine)
        except ImportError as exc:
            log.warning("TTS engine %r unavailable (%s). Falling back to the browser voice. "
                        "Install it with: pip install edge-tts", engine, exc)
            self._browser = True
        log.info("Voice announcer: engine=%s voice_fa=%s",
                 engine, getattr(self.s, "tts_voice_fa", "-"))

    def _cleanup_cache(self) -> None:
        cutoff = time.time() - 3600
        for f in AUDIO_DIR.glob("*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    async def run(self) -> None:
        self._make_backend()
        self._cleanup_cache()
        while True:
            item: _Item = await self.queue.get()
            if time.monotonic() - item.created > item.ttl or not self.has_clients():
                continue
            url, duration, browser = None, 0.0, False
            if not self.muted:
                if self._backend is not None:
                    try:
                        path, duration = await self._backend.synth(item.text)
                        url = f"/audio/{path.name}"
                    except Exception as exc:
                        log.warning("Speech synthesis failed: %s", exc)
                        browser = self.s.browser_tts_fallback
                elif self._browser:
                    browser = True
            if not duration:
                duration = estimate_duration(item.text)
            log.info("[VOICE] %s", item.text)
            self.emit({"type": "speak", "text": item.text, "kind": item.kind, "url": url,
                       "browser_tts": browser, "duration": round(duration, 2)})
            await asyncio.sleep(duration + 0.4)