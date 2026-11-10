"""
Chat sources - everything that can produce `ChatMessage` objects.

  * AparatSource       Aparat live chat via Playwright (headless Chromium) - DEFAULT
  * DemoSource         fake viewers, so the whole game can be tested offline
  * PytchatSource      YouTube live chat with the `pytchat` package (no API key)
  * YouTubeApiSource   YouTube live chat with the official Data API v3

Every source exposes:  `async def run(self, on_message)`  where `on_message`
is a normal (sync) callable that receives one `ChatMessage`.

`ChatMessage.kind` is one of:
  * "text"       normal chat message
  * "ruby"       Aparat ruby (replaces YouTube Super Chat)
  * "superchat"  YouTube Super Chat
  * "sticker"    YouTube Super Sticker
  * "member"     new member / gifted membership
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from config import BROWSER_DIR, Settings, USD_RATES
from streamers import StreamerIndex

log = logging.getLogger("chat")

# observer.js lives next to this file
_OBSERVER_JS = (Path(__file__).resolve().parent / "observer.js").read_text(encoding="utf-8")


@dataclass
class ChatMessage:
    id: str
    author_id: str
    author: str
    text: str = ""
    avatar: str = ""
    kind: str = "text"          # text | ruby | superchat | sticker | member
    amount_usd: float = 0.0
    amount_text: str = ""
    ruby_count: int = 0
    ruby_color: str = ""
    count: int = 1
    is_owner: bool = False
    is_mod: bool = False
    is_member: bool = False


_SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR",
    "₺": "TRY", "₽": "RUB", "₩": "KRW", "R$": "BRL", "CA$": "CAD", "A$": "AUD",
    "NZ$": "NZD", "MX$": "MXN", "₱": "PHP", "₫": "VND", "Rp": "IDR", "฿": "THB",
    "zł": "PLN", "₪": "ILS", "HK$": "HKD", "NT$": "TWD", "S$": "SGD", "₦": "NGN",
}
_warned_currencies: set[str] = set()


def to_usd(amount: float, currency: str) -> float:
    cur = (currency or "").strip()
    code = _SYMBOLS.get(cur, cur.upper())
    rate = USD_RATES.get(code)
    if rate is None:
        if code not in _warned_currencies:
            _warned_currencies.add(code)
            log.warning("Unknown currency %r - counting it as 0.01 USD per unit. "
                        "Add it to USD_RATES in config.py", currency)
        rate = 0.01
    return float(amount) * rate


_ID_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def extract_video_id(value: str) -> str:
    value = (value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    m = _ID_RE.search(value)
    return m.group(1) if m else value


# --------------------------------------------------------------------------- #
# Demo source (uses streamers from the registry)
# --------------------------------------------------------------------------- #
class DemoSource:
    USERS = [
        "Ali_Gamer", "علی رضایی", "Sara", "سارة", "Mohammad", "Carlos", "Putri", "Aditya",
        "Emma", "Kenji", "Fatima", "Dmitri", "Zeynep", "Hassan", "Lucas", "Nguyen",
        "AlbCro22", "User R", "Maria", "Omar", "رضا", "Parisa", "Tariq", "ليلى", "John",
        "Priya", "Sven", "Chloe", "Ahmed", "Yuki", "نگار", "Bilal", "Anna", "Mehdi",
    ]
    CHATTER = ["hello everyone", "who is winning?", "great stream!", "سلام", "gg",
               "lets goooo", "how do I vote?", "مرحبا"]

    def __init__(self, settings: Settings, index: StreamerIndex):
        self.s = settings
        self.index = index
        self.favorite: dict[str, str] = {}

    def _pick_streamer(self, user: str) -> str | None:
        names = [s.username for s in self.index.all()]
        if not names:
            return None
        if user not in self.favorite:
            self.favorite[user] = random.choice(names)
        if random.random() < 0.85:
            return self.favorite[user]
        return random.choice(names)

    def _text_for(self, username: str) -> str:
        st = self.index.get(username)
        if not st:
            return username
        pool = [st.display] + list(st.keywords)
        if not pool:
            return username
        return random.choice(pool)

    async def run(self, on_message) -> None:
        log.info("DEMO MODE - fake viewers are chatting (use --source aparat to go live)")
        next_ruby = time.monotonic() + 15
        next_member = time.monotonic() + 60
        while True:
            await asyncio.sleep(random.expovariate(max(0.2, self.s.demo_rate)))
            now = time.monotonic()
            user = random.choice(self.USERS)
            uid = f"demo:{user}"
            username = self._pick_streamer(user)
            mid = uuid.uuid4().hex
            if username is None:
                on_message(ChatMessage(id=mid, author_id=uid, author=user,
                                       text=random.choice(self.CHATTER)))
                continue
            if now >= next_ruby:
                next_ruby = now + random.uniform(25, 60)
                n = random.choice([5, 10, 20, 30, 50, 100])
                on_message(ChatMessage(
                    id=mid, author_id=uid, author=user,
                    text=self._text_for(username),
                    kind="ruby", ruby_count=n,
                    ruby_color="rgb(8, 97, 210)",
                    amount_text=f"{n}"))
            elif now >= next_member:
                next_member = now + random.uniform(45, 90)
                on_message(ChatMessage(id=mid, author_id=uid, author=user, kind="member"))
            elif random.random() < 0.12:
                on_message(ChatMessage(id=mid, author_id=uid, author=user,
                                       text=random.choice(self.CHATTER)))
            else:
                on_message(ChatMessage(id=mid, author_id=uid, author=user,
                                       text=self._text_for(username)))


# --------------------------------------------------------------------------- #
# Aparat source (Playwright) - DEFAULT, with resource optimization
# --------------------------------------------------------------------------- #
def _build_aparat_init_script(cfg: dict) -> str:
    keys = ("message_selector", "user_selector", "text_selector", "warmup_seconds")
    payload = json.dumps({k: cfg.get(k) for k in keys}, ensure_ascii=False)
    return (
        "(() => {\n" + _OBSERVER_JS + "\n"
        f"const CFG = {payload};\n"
        "const run = () => {\n"
        "  if (window.__sb_push) {\n"
        "    __sbStart(CFG, (u, t, m) => window.__sb_push(u, t, m || { kind: 'text', amount: 0, color: '' }));\n"
        "  }\n"
        "};\n"
        "if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);\n"
        "else run();\n"
        "})();"
    )


# Arguments passed to Chromium to reduce memory + CPU footprint.
_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--mute-audio",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints,CalculateNativeWinOcclusion",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-component-update",
    "--disable-client-side-phishing-detection",
    "--disable-popup-blocking",
    "--disable-push-messaging",
    "--disable-notifications",
    "--disable-speech-api",
    "--disable-webgl",
    "--disable-2d-canvas-clip-aa",
    "--disable-accelerated-2d-canvas",
    "--disable-accelerated-video-decode",
    "--disable-software-rasterizer",
    "--disable-logging",
    "--log-level=3",
    "--js-flags=--max-old-space-size=256",
    "--renderer-process-limit=2",
    "--blink-settings=imagesEnabled=false",
]


class AparatSource:
    """Reads an Aparat live chat with a headless Chromium (Playwright).

    Resource optimization:
      * Images are disabled via Blink (chat avatars are not needed; only the
        chat text is used).
      * The page's own video/audio elements are paused and their sources
        removed once the page loads.
      * Extra browser tabs are closed.
      * The main loop polls every 5s instead of 2s.
    """

    def __init__(self, settings: Settings):
        self.s = settings

    async def run(self, on_message) -> None:
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            log.error("playwright is not installed.  "
                      "Run:  pip install playwright  &&  playwright install chromium")
            return

        backoff = 8
        while True:
            try:
                await self._session(on_message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("Aparat source error: %r - retrying in %ss", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def _session(self, on_message) -> None:
        from playwright.async_api import async_playwright

        s = self.s
        if not s.aparat_channel_url:
            log.error("Aparat URL is empty.  "
                      "Run:  python main.py --aparat-url <URL>  "
                      "or set APARAT_URL in your .env")
            await asyncio.sleep(3600)
            return

        cfg = {
            "message_selector": s.aparat_message_selector,
            "user_selector": s.aparat_user_selector,
            "text_selector": s.aparat_text_selector,
            "warmup_seconds": s.aparat_warmup_seconds,
        }

        BROWSER_DIR.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                str(BROWSER_DIR),
                headless=s.aparat_headless,
                locale="fa-IR",
                viewport={"width": 1280, "height": 800},
                args=list(_CHROMIUM_ARGS),
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                # Close any extra tabs that the profile might have restored.
                try:
                    for extra in list(ctx.pages):
                        if extra is not page:
                            await extra.close()
                except Exception:
                    pass

                loop = asyncio.get_running_loop()

                async def _push(user: str, text: str, meta: dict) -> None:
                    if s.aparat_debug:
                        log.info("[aparat] %s: %s | %s", user, text, meta)
                    kind = (meta or {}).get("kind", "text")
                    if kind == "ruby":
                        n = int((meta or {}).get("amount") or 0)
                        color = (meta or {}).get("color") or ""
                        on_message(ChatMessage(
                            id=uuid.uuid4().hex,
                            author_id=f"aparat:{user}",
                            author=user, text=text,
                            kind="ruby", ruby_count=max(1, n),
                            ruby_color=color,
                            amount_text=str(n)))
                    else:
                        on_message(ChatMessage(
                            id=uuid.uuid4().hex,
                            author_id=f"aparat:{user}",
                            author=user, text=text))

                def sync_push(user: str, text: str, meta: dict) -> None:
                    loop.create_task(_push(user, text, meta))

                if s.aparat_mode == "ws":
                    page.on("websocket", lambda ws: self._on_ws(ws, on_message))
                else:
                    await page.expose_function("__sb_push", sync_push)
                    await ctx.add_init_script(_build_aparat_init_script(cfg))

                log.info("Opening Aparat live page (%s mode): %s",
                         s.aparat_mode, s.aparat_channel_url)
                await page.goto(s.aparat_channel_url, wait_until="domcontentloaded",
                                timeout=60_000)

                # ---- Freeze the video player & stop any audio/video loading ----
                try:
                    await page.evaluate("""
                        () => {
                            // Pause and detach any <video>/<audio> element.
                            document.querySelectorAll('video, audio').forEach(v => {
                                try {
                                    v.pause();
                                    v.muted = true;
                                    v.removeAttribute('src');
                                    v.querySelectorAll('source').forEach(s => s.remove());
                                    v.load();
                                } catch (e) {}
                            });
                            // Also stop any MediaSource activity.
                            try {
                                if (window.MediaSource) {
                                    MediaSource.isTypeSupported = function () { return false; };
                                }
                            } catch (e) {}
                            // Mark images as lazy so they don't load.
                            document.querySelectorAll('img').forEach(img => {
                                try { img.loading = 'lazy'; } catch (e) {}
                            });
                        }
                    """)
                except Exception as exc:
                    log.debug("Could not freeze media: %s", exc)

                # Re-apply media freeze every 30 seconds (Aparat may add a new player).
                async def _freeze_loop():
                    while not page.is_closed():
                        await asyncio.sleep(30)
                        try:
                            await page.evaluate("""
                                () => {
                                    document.querySelectorAll('video, audio').forEach(v => {
                                        try { v.pause(); v.muted = true; } catch (e) {}
                                    });
                                }
                            """)
                        except Exception:
                            return
                freeze_task = asyncio.create_task(_freeze_loop())

                log.info("Aparat page loaded - listening for chat ...")
                try:
                    while not page.is_closed():
                        await asyncio.sleep(5)
                finally:
                    freeze_task.cancel()
            finally:
                await ctx.close()

    # --------------------------- websocket sniffing mode -------------------
    TEXT_KEYS = ("message", "text", "body", "content", "msg", "comment")
    USER_KEYS = ("username", "user_name", "nickname", "display_name",
                 "name", "sender", "from", "author", "user")

    def _on_ws(self, ws, on_message) -> None:
        flt = self.s.aparat_ws_url_filter
        if flt and flt not in ws.url:
            return
        log.info("Aparat WebSocket opened: %s", ws.url[:120])
        loop = asyncio.get_running_loop()

        def on_frame(payload) -> None:
            for user, text in _parse_aparat_frame(payload):
                loop.create_task(_safe_call(on_message, user, text))

        ws.on("framereceived", on_frame)


async def _safe_call(on_message, user: str, text: str) -> None:
    try:
        on_message(ChatMessage(id=uuid.uuid4().hex, author_id=f"aparat:{user}",
                               author=user, text=text))
    except Exception:
        log.exception("Aparat WS callback failed")


def _aparat_user_of(d: dict) -> str | None:
    for k in AparatSource.USER_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            inner = _aparat_user_of(v)
            if inner:
                return inner
    return None


def _aparat_extract(obj) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        text = next((obj[k] for k in AparatSource.TEXT_KEYS
                     if isinstance(obj.get(k), str) and obj[k].strip()), None)
        if text:
            user = _aparat_user_of(obj)
            if user:
                out.append((user, text))
                return out
        for v in obj.values():
            out.extend(_aparat_extract(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_aparat_extract(v))
    return out


def _parse_aparat_frame(payload) -> list[tuple[str, str]]:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return []
    out: list[tuple[str, str]] = []
    for line in str(payload).splitlines() or [str(payload)]:
        m = re.search(r"[\[{]", line)
        if not m:
            continue
        try:
            out.extend(_aparat_extract(json.loads(line[m.start():])))
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------------------- #
# YouTube sources (kept so you can still use them if you want)
# --------------------------------------------------------------------------- #
class PytchatSource:
    def __init__(self, settings: Settings):
        self.s = settings
        self.video_id = extract_video_id(settings.video)

    async def run(self, on_message) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        thread = threading.Thread(target=self._worker, args=(loop, queue, stop),
                                  name="pytchat", daemon=True)
        thread.start()
        try:
            while True:
                msg = await queue.get()
                on_message(msg)
        finally:
            stop.set()

    def _worker(self, loop, queue, stop: threading.Event) -> None:
        try:
            import pytchat
        except ImportError:
            log.error("pytchat is not installed.  Run: pip install pytchat")
            return
        backoff = 3
        while not stop.is_set():
            try:
                chat = pytchat.create(video_id=self.video_id, interruptable=False)
                log.info("Connected to the live chat of video %s", self.video_id)
                backoff = 3
                while chat.is_alive() and not stop.is_set():
                    data = chat.get()
                    if not data:
                        time.sleep(1)
                        continue
                    for item in data.sync_items():
                        if stop.is_set():
                            break
                        msg = self._convert(item)
                        if msg:
                            loop.call_soon_threadsafe(queue.put_nowait, msg)
                log.warning("Chat stream stopped - retrying in %ss ...", backoff)
            except Exception as exc:
                log.error("pytchat error: %s - retrying in %ss", exc, backoff)
            for _ in range(backoff * 2):
                if stop.is_set():
                    return
                time.sleep(0.5)
            backoff = min(backoff * 2, 60)

    @staticmethod
    def _convert(c) -> ChatMessage | None:
        kind = {"textMessage": "text", "superChat": "superchat",
                "superSticker": "sticker", "newSponsor": "member"}.get(c.type)
        if kind is None:
            return None
        usd = 0.0
        if kind in ("superchat", "sticker"):
            usd = to_usd(getattr(c, "amountValue", 0) or 0, getattr(c, "currency", "") or "")
        a = c.author
        return ChatMessage(
            id=str(c.id), author_id=str(a.channelId), author=str(a.name or "viewer"),
            text=str(c.message or ""), avatar=str(getattr(a, "imageUrl", "") or ""),
            kind=kind, amount_usd=usd, amount_text=str(getattr(c, "amountString", "") or ""),
            is_owner=bool(getattr(a, "isChatOwner", False)),
            is_mod=bool(getattr(a, "isChatModerator", False)),
            is_member=bool(getattr(a, "isChatSponsor", False)),
        )


class YouTubeApiSource:
    BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, settings: Settings):
        self.s = settings
        self.video_id = extract_video_id(settings.video)

    async def run(self, on_message) -> None:
        import aiohttp
        if not self.s.api_key:
            log.error("backend=api needs an API key")
            return
        backoff = 5
        first_connect = True
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as http:
            while True:
                try:
                    chat_id = await self._chat_id(http)
                    log.info("Connected to live chat %s (official API)", chat_id)
                    page_token = None
                    skip_page = first_connect
                    first_connect = False
                    backoff = 5
                    while True:
                        params = {"liveChatId": chat_id, "part": "snippet,authorDetails",
                                  "maxResults": "200", "key": self.s.api_key}
                        if page_token:
                            params["pageToken"] = page_token
                        async with http.get(f"{self.BASE}/liveChat/messages", params=params) as r:
                            data = await r.json()
                            if r.status != 200:
                                reason = data.get("error", {}).get("message", r.status)
                                raise RuntimeError(f"YouTube API error: {reason}")
                        page_token = data.get("nextPageToken")
                        if not skip_page:
                            for item in data.get("items", []):
                                msg = self._convert(item)
                                if msg:
                                    on_message(msg)
                        skip_page = False
                        wait = max(data.get("pollingIntervalMillis", 5000) / 1000,
                                   self.s.api_poll_seconds)
                        await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("%s - retrying in %ss", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)

    async def _chat_id(self, http) -> str:
        params = {"part": "liveStreamingDetails", "id": self.video_id, "key": self.s.api_key}
        async with http.get(f"{self.BASE}/videos", params=params) as r:
            data = await r.json()
        items = data.get("items") or []
        if not items:
            raise RuntimeError(f"Video {self.video_id!r} not found")
        chat_id = items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
        if not chat_id:
            raise RuntimeError("This video has no active live chat")
        return chat_id

    @staticmethod
    def _convert(item: dict) -> ChatMessage | None:
        sn, au = item.get("snippet", {}), item.get("authorDetails", {})
        t = sn.get("type")
        base = dict(id=item.get("id", uuid.uuid4().hex), author_id=au.get("channelId", ""),
                    author=au.get("displayName", "viewer"), avatar=au.get("profileImageUrl", ""),
                    is_owner=bool(au.get("isChatOwner")), is_mod=bool(au.get("isChatModerator")),
                    is_member=bool(au.get("isChatSponsor")))
        if t == "textMessageEvent":
            return ChatMessage(text=sn.get("displayMessage", ""), **base)
        if t in ("superChatEvent", "superStickerEvent"):
            d = sn.get("superChatDetails") or sn.get("superStickerDetails") or {}
            amount = int(d.get("amountMicros", 0)) / 1_000_000
            return ChatMessage(
                text=d.get("userComment", "") or "",
                kind="superchat" if t == "superChatEvent" else "sticker",
                amount_usd=to_usd(amount, d.get("currency", "USD")),
                amount_text=d.get("amountDisplayString", ""), **base)
        if t == "newSponsorEvent":
            return ChatMessage(kind="member", **base)
        if t == "membershipGiftingEvent":
            n = int((sn.get("membershipGiftingDetails") or {}).get("giftMembershipsCount", 1))
            return ChatMessage(kind="member", count=max(1, n), **base)
        return None


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def make_source(settings: Settings, index: StreamerIndex):
    """Pick the chat source based on settings.source (default: aparat)."""
    src = (settings.source or "aparat").lower()
    if settings.demo:
        src = "demo"

    if src == "aparat":
        return AparatSource(settings)

    if src == "youtube":
        if not settings.video:
            log.warning("source=youtube but no video given - starting DEMO mode")
            return DemoSource(settings, index)
        if settings.backend == "api":
            return YouTubeApiSource(settings)
        return PytchatSource(settings)

    if src == "off":
        return _OffSource()

    return DemoSource(settings, index)


class _OffSource:
    """Does nothing - useful when you only feed chat through /api/ingest."""
    async def run(self, on_message) -> None:
        log.info("Chat source is OFF - use the ingest API or the userscript")
        while True:
            await asyncio.sleep(3600)