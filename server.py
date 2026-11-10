"""
Web server: serves the animated overlay, pushes game events over a WebSocket,
serves streamer / voter profile pictures locally, exposes the control / admin API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

from chat_sources import ChatMessage
from config import (APARAT_PROFILE_API, AUDIO_DIR, DATA_DIR, PICS_DIR,
                    STATIC_DIR, Settings)
from streamers import StreamerIndex

log = logging.getLogger("server")

LOCAL_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

DEFAULT_THEME = {
    "accent": "#7c5cff",
    "title_color": "#ffffff",
    "bg_top": "#34196e",
    "bg_bottom": "#05040f",
    "card_radius": "12px",
    "score_size": "17px",
    "ruby_color": "#0052b3",
}


@web.middleware
async def no_cache(request: web.Request, handler):
    resp = await handler(request)
    p = request.path
    if p == "/" or p.startswith(("/static", "/admin", "/add", "/data", "/pics")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class OverlayServer:
    def __init__(self, settings: Settings, index: StreamerIndex):
        self.s = settings
        self.index = index
        self.engine = None
        self.announcer = None
        self.clients: set[web.WebSocketResponse] = set()
        self.admin_clients: set[web.WebSocketResponse] = set()
        self._outbox: asyncio.Queue = asyncio.Queue()
        self._http: aiohttp.ClientSession | None = None
        self._pic_sem = asyncio.Semaphore(6)
        self._tasks: list[asyncio.Task] = []
        self.theme: dict = dict(DEFAULT_THEME)
        self.reload_sources = None
        self._voter_pic_failed: set[str] = set()

    def bind(self, engine, announcer, reload_sources=None) -> None:
        self.engine = engine
        self.announcer = announcer
        self.reload_sources = reload_sources

    def emit(self, msg: dict) -> None:
        self._outbox.put_nowait(msg)

    def has_clients(self) -> bool:
        return bool(self.clients)

    async def _sender(self) -> None:
        while True:
            msg = await self._outbox.get()
            if not self.clients:
                continue
            data = json.dumps(msg, ensure_ascii=False)
            await asyncio.gather(*(self._send(ws, data) for ws in list(self.clients)))

    async def _send(self, ws: web.WebSocketResponse, data: str) -> None:
        try:
            await ws.send_str(data)
        except Exception:
            self.clients.discard(ws)

    # -------------------------------------------------------------- payloads
    def _streamer_list(self) -> list[dict]:
        out = []
        for s in self.index.all():
            out.append({
                "username": s.username,
                "display": s.display,
                "keywords": list(s.keywords),
                "score": str(s.score),
                "pic": s.pic,
                "pic_url": s.pic_url,
                "pic_src": f"/pics/{s.pic}" if s.pic else "",
            })
        return out

    def ui_config(self) -> dict:
        s = self.s
        return {"title": s.title, "ruby_points": s.ruby_points,
                "member_points": s.member_points, "max_level": s.max_level,
                "voice": s.tts_engine != "off",
                "language": s.language, "theme": self.theme}

    def init_payload(self) -> dict:
        return {"type": "init", "config": self.ui_config(),
                "streamers": self._streamer_list(),
                **self.engine.init_payload()}

    def admin_payload(self) -> dict:
        data = self.engine.state_payload()
        s = self.s
        data["muted"] = self.announcer.muted
        data["overlays"] = len(self.clients)
        data["tts_engine"] = s.tts_engine
        data["tts_voice"] = s.tts_voice
        data["tts_voice_fa"] = getattr(s, "tts_voice_fa", "")
        data["streamers"] = self._streamer_list()
        data["settings"] = {
            "user_cooldown": s.user_cooldown,
            "ruby_points": s.ruby_points,
            "ruby_min_points": s.ruby_min_points,
            "member_points": s.member_points,
            "fire_points": s.fire_points,
            "standings_minutes": s.standings_minutes,
            "reminder_minutes": s.reminder_minutes,
            "source": s.source,
            "language": s.language,
            "aparat_channel_url": s.aparat_channel_url,
            "aparat_mode": s.aparat_mode,
            "aparat_headless": s.aparat_headless,
            "aparat_warmup_seconds": s.aparat_warmup_seconds,
            "aparat_message_selector": s.aparat_message_selector,
            "aparat_user_selector": s.aparat_user_selector,
            "aparat_text_selector": s.aparat_text_selector,
            "aparat_ws_url_filter": s.aparat_ws_url_filter,
            "aparat_debug": s.aparat_debug,
            "admin_password": s.admin_password,
            "booster_target": s.booster_target,
            "booster_duration": s.booster_duration,
            "booster_multiplier": s.booster_multiplier,
        }
        data["theme"] = self.theme
        return data

    # ---------------------------------------------------------------- routes
    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[no_cache])
        app.add_routes([
            web.get("/", self.page_index),
            web.get("/admin", self.page_admin),
            web.get("/add", self.page_add),
            web.get("/ws", self.ws_handler),
            web.get("/ws/admin", self.ws_admin_handler),
            web.get("/data/streamers.json", self.streamers_json),
            web.get("/pics/{name}", self.pic),
            web.get("/api/state", self.api_state),
            web.post("/api/admin/login", self.api_admin_login),
            web.post("/api/simulate", self.api_simulate),
            web.post("/api/reset", self.api_reset),
            web.post("/api/add", self.api_add),
            web.post("/api/set_points", self.api_set_points),
            web.post("/api/set_user_points", self.api_set_user_points),
            web.post("/api/ban", self.api_ban),
            web.post("/api/pause", self.api_pause),
            web.post("/api/undo", self.api_undo),
            web.post("/api/clear_queue", self.api_clear_queue),
            web.post("/api/ruby", self.api_ruby),
            web.post("/api/say", self.api_say),
            web.post("/api/mute", self.api_mute),
            web.post("/api/settings", self.api_settings),
            web.post("/api/voice", self.api_voice),
            web.post("/api/test_voice", self.api_test_voice),
            web.post("/api/theme", self.api_theme),
            web.post("/api/clear_achievements", self.api_clear_achievements),
            web.post("/api/ingest", self.api_ingest),
            web.post("/api/voter_pic", self.api_voter_pic),
            web.post("/api/booster", self.api_booster),
            web.get("/userscript.user.js", self.userscript),
            web.get("/api/streamers", self.api_streamers_list),
            web.post("/api/streamers/lookup", self.api_streamers_lookup),
            web.post("/api/streamers/add", self.api_streamers_add),
            web.post("/api/streamers/update", self.api_streamers_update),
            web.post("/api/streamers/remove", self.api_streamers_remove),
        ])
        app.router.add_static("/static", STATIC_DIR, show_index=False)
        app.router.add_static("/audio", AUDIO_DIR, show_index=False)
        return app

    async def start(self) -> web.AppRunner:
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        runner = web.AppRunner(self.build_app(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, self.s.host, self.s.port).start()
        self._tasks = [asyncio.create_task(self._sender())]
        return runner

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._http:
            await self._http.close()

    # ----------------------------------------------------------------- pages
    async def page_index(self, request: web.Request):
        return web.FileResponse(STATIC_DIR / "index.html")

    async def page_admin(self, request: web.Request):
        return web.FileResponse(STATIC_DIR / "admin.html")

    async def page_add(self, request: web.Request):
        return web.FileResponse(STATIC_DIR / "add.html")

    async def streamers_json(self, request: web.Request):
        return web.json_response(self._streamer_list())

    # --------------------------------------------------------------- pictures
    async def pic(self, request: web.Request):
        name = request.match_info["name"]
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPNotFound()
        path = PICS_DIR / name
        if not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})

    async def _download_pic(self, url: str, username: str) -> str:
        if not url:
            return ""
        PICS_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", username)[:60] or "streamer"
        ext = ".jpg"
        m = re.search(r"\.(jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I)
        if m:
            ext = "." + m.group(1).lower()
        local = f"{safe}{ext}"
        path = PICS_DIR / local
        async with self._pic_sem:
            try:
                async with self._http.get(url) as r:
                    if r.status != 200:
                        log.warning("Could not download pic %s (HTTP %s)", url, r.status)
                        return ""
                    data = await r.read()
                path.write_bytes(data)
                return local
            except Exception as exc:
                log.warning("Could not download pic %s: %s", url, exc)
                return ""

    async def _download_voter_pic(self, username: str) -> str:
        if not username or "/" in username or " " in username or "@" in username:
            return ""
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", username)[:60]
        if not safe:
            return ""
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            p = PICS_DIR / f"v_{safe}{ext}"
            if p.exists() and p.stat().st_size > 200:
                return f"v_{safe}{ext}"
        if safe in self._voter_pic_failed:
            return ""
        try:
            async with self._http.get(APARAT_PROFILE_API.format(username=username)) as r:
                if r.status != 200:
                    self._voter_pic_failed.add(safe)
                    return ""
                payload = await r.json()
        except Exception as exc:
            log.debug("voter pic lookup failed for %s: %s", username, exc)
            return ""
        profile = (payload or {}).get("profile") or {}
        url = profile.get("pic_s") or profile.get("pic_m") or profile.get("pic_b") or ""
        if not url:
            self._voter_pic_failed.add(safe)
            return ""
        ext = ".jpg"
        m = re.search(r"\.(jpg|jpeg|png|webp|gif)(?:\?|$)", url, re.I)
        if m:
            ext = "." + m.group(1).lower()
        local = f"v_{safe}{ext}"
        path = PICS_DIR / local
        async with self._pic_sem:
            try:
                async with self._http.get(url) as r:
                    if r.status != 200:
                        self._voter_pic_failed.add(safe)
                        return ""
                    path.write_bytes(await r.read())
                return local
            except Exception as exc:
                log.debug("voter pic download failed for %s: %s", username, exc)
                self._voter_pic_failed.add(safe)
                return ""

    # ------------------------------------------------------------------- ws
    async def ws_handler(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        await ws.send_str(json.dumps(self.init_payload(), ensure_ascii=False))
        self.clients.add(ws)
        log.info("Overlay connected (%d open)", len(self.clients))
        try:
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)
            log.info("Overlay disconnected (%d open)", len(self.clients))
        return ws

    async def ws_admin_handler(self, request: web.Request):
        if request.remote not in LOCAL_IPS:
            raise web.HTTPForbidden(text="The admin socket is only available from this computer.")
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        self.admin_clients.add(ws)
        log.info("Admin connected (%d open)", len(self.admin_clients))
        try:
            await ws.send_str(json.dumps(self.admin_payload(), ensure_ascii=False))
            while not ws.closed:
                await asyncio.sleep(2.0)
                try:
                    await ws.send_str(json.dumps(self.admin_payload(), ensure_ascii=False))
                except Exception:
                    break
        finally:
            self.admin_clients.discard(ws)
            log.info("Admin disconnected (%d open)", len(self.admin_clients))
        return ws

    # ------------------------------------------------------------ control API
    @staticmethod
    def _local_only(request: web.Request) -> None:
        if request.remote not in LOCAL_IPS:
            raise web.HTTPForbidden(text="The control API is only available from this computer.")

    async def api_state(self, request: web.Request):
        self._local_only(request)
        return web.json_response(self.admin_payload())

    async def api_admin_login(self, request: web.Request):
        data = await request.json()
        if str(data.get("password") or "") != self.s.admin_password:
            raise web.HTTPUnauthorized(text="bad password")
        return web.json_response({"ok": True})

    async def api_simulate(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        user = str(data.get("user") or "TestUser")[:40]
        kind = data.get("kind", "text")
        if kind not in ("text", "ruby", "superchat", "member"):
            kind = "text"
        n = int(data.get("ruby") or data.get("usd") or 0)
        self.engine.handle_message(ChatMessage(
            id=uuid.uuid4().hex, author_id=f"sim:{user}", author=user,
            text=str(data.get("text", ""))[:200], kind=kind,
            ruby_count=n if kind == "ruby" else 0,
            amount_usd=float(data.get("usd") or 0),
            amount_text=f"{n}" if kind == "ruby" else (f"${n:.2f}" if n else "")))
        return web.json_response({"ok": True})

    async def api_reset(self, request: web.Request):
        self._local_only(request)
        self.engine.reset()
        return web.json_response({"ok": True})

    async def api_add(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_add(str(data.get("code", "")), int(data.get("points", 0)))
        return web.json_response({"ok": ok})

    async def api_set_points(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_set_points(str(data.get("code", "")), int(data.get("points", 0)))
        return web.json_response({"ok": ok})

    async def api_set_user_points(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_set_user_points(str(data.get("uid", "")), int(data.get("points", 0)))
        return web.json_response({"ok": ok})

    async def api_ban(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_ban(str(data.get("uid", "")), bool(data.get("banned", True)))
        return web.json_response({"ok": ok})

    async def api_pause(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_pause(bool(data.get("paused", True)))
        return web.json_response({"ok": ok})

    async def api_undo(self, request: web.Request):
        self._local_only(request)
        ok = self.engine.admin_undo()
        return web.json_response({"ok": ok})

    async def api_clear_queue(self, request: web.Request):
        self._local_only(request)
        self.engine.admin_clear_queue()
        return web.json_response({"ok": True})

    async def api_ruby(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        ok = self.engine.admin_confirm_ruby(
            str(data.get("user", "ManualUser")),
            str(data.get("code", "")),
            int(data.get("ruby", 1)))
        return web.json_response({"ok": ok})

    async def api_booster(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        action = str(data.get("action") or "")
        ok = self.engine.admin_booster(action)
        return web.json_response({"ok": ok, "booster": self.engine.booster_payload()["booster"]})

    async def api_clear_achievements(self, request: web.Request):
        self._local_only(request)
        self.engine.admin_clear_achievements()
        return web.json_response({"ok": True})

    async def api_say(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        text = str(data.get("text", "")).strip()[:300]
        if text:
            self.announcer.say(text, kind="admin", priority=0, ttl=60)
        return web.json_response({"ok": bool(text)})

    async def api_mute(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        self.announcer.muted = bool(data.get("muted"))
        return web.json_response({"ok": True, "muted": self.announcer.muted})

    async def api_test_voice(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        text = str(data.get("text", "")).strip() or "این یک تست صدای گوینده است."
        self.announcer.test_voice(text)
        return web.json_response({"ok": True})

    async def api_voice(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        self.announcer.set_voices(
            voice_en=data.get("voice_en") or None,
            voice_fa=data.get("voice_fa") or None)
        return web.json_response({"ok": True,
                                  "voice_en": self.s.tts_voice,
                                  "voice_fa": getattr(self.s, "tts_voice_fa", "")})

    async def api_settings(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        s = self.s
        restart_needed = False
        booster_changed = False
        for key, cast in (
            ("user_cooldown", float),
            ("ruby_points", int),
            ("ruby_min_points", int),
            ("member_points", int),
            ("fire_points", int),
            ("standings_minutes", float),
            ("reminder_minutes", float),
        ):
            if key in data and data[key] is not None:
                try:
                    setattr(s, key, cast(data[key]))
                except (TypeError, ValueError):
                    pass
        for key, cast in (
            ("booster_target", int),
            ("booster_duration", float),
            ("booster_multiplier", int),
        ):
            if key in data and data[key] is not None:
                try:
                    setattr(s, key, cast(data[key]))
                    booster_changed = True
                except (TypeError, ValueError):
                    pass
        for key in ("aparat_channel_url", "aparat_message_selector",
                    "aparat_user_selector", "aparat_text_selector",
                    "aparat_ws_url_filter"):
            if key in data and data[key] is not None:
                setattr(s, key, str(data[key]))
                restart_needed = True
        for key in ("aparat_mode", "language", "source"):
            if key in data and data[key] is not None:
                setattr(s, key, str(data[key]).lower())
                restart_needed = True
        for key in ("aparat_headless", "aparat_debug"):
            if key in data and data[key] is not None:
                setattr(s, key, bool(data[key]))
                restart_needed = True
        if "aparat_warmup_seconds" in data and data["aparat_warmup_seconds"] is not None:
            try:
                s.aparat_warmup_seconds = int(data["aparat_warmup_seconds"])
                restart_needed = True
            except (TypeError, ValueError):
                pass
        if "admin_password" in data and data["admin_password"]:
            s.admin_password = str(data["admin_password"])

        if restart_needed and self.reload_sources:
            try:
                await self.reload_sources()
            except Exception as exc:
                log.warning("Could not restart the chat source: %s", exc)

        if booster_changed and self.engine is not None:
            self.engine.emit(self.engine.booster_payload() | {"type": "booster"})

        return web.json_response({"ok": True, "settings": self.admin_payload()["settings"]})

    async def api_theme(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        for k, v in (data or {}).items():
            if k in DEFAULT_THEME and isinstance(v, str):
                self.theme[k] = v[:40]
        self.emit({"type": "theme", "theme": self.theme})
        return web.json_response({"ok": True, "theme": self.theme})

    async def api_ingest(self, request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        items = body if isinstance(body, list) else [body]
        n = 0
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            user = str(item.get("user", "viewer"))[:40]
            text = str(item.get("text", ""))[:300]
            kind = item.get("kind", "text")
            if kind == "ruby":
                self.engine.handle_message(ChatMessage(
                    id=uuid.uuid4().hex, author_id=f"aparat:{user}", author=user,
                    text=text, kind="ruby",
                    ruby_count=max(1, int(item.get("amount") or 1)),
                    ruby_color=str(item.get("color") or ""),
                    amount_text=str(item.get("amount") or "")))
            else:
                self.engine.handle_message(ChatMessage(
                    id=uuid.uuid4().hex, author_id=f"aparat:{user}", author=user, text=text))
            n += 1
        return web.json_response({"ok": True, "count": n})

    async def api_voter_pic(self, request: web.Request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        username = str(data.get("username") or "").strip().lstrip("@")
        if not username:
            return web.json_response({"ok": False, "error": "missing username"}, status=400)
        cached = self.index.voter_pic_filename(username)
        if cached:
            return web.json_response({"ok": True, "src": f"/pics/{cached}"})
        local = await self._download_voter_pic(username)
        if local:
            return web.json_response({"ok": True, "src": f"/pics/{local}"})
        return web.json_response({"ok": False})

    async def userscript(self, request: web.Request):
        base = f"{request.url.scheme}://{request.headers.get('host', '127.0.0.1:8000')}"
        try:
            obs = (STATIC_DIR.parent / "observer.js").read_text(encoding="utf-8")
        except OSError:
            obs = "/* observer.js not found */"
        s = self.s
        cfg = json.dumps({
            "message_selector": s.aparat_message_selector,
            "user_selector": s.aparat_user_selector,
            "text_selector": s.aparat_text_selector,
            "warmup_seconds": 3,
        }, ensure_ascii=False)
        js = f"""// ==UserScript==
// @name         Streamer Boost - Aparat chat bridge
// @namespace    streamer-boost
// @version      1.0
// @description  Sends Aparat live chat + rubies to your local Streamer Boost server
// @match        https://www.aparat.com/*
// @match        https://aparat.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==
(function () {{
{obs}
var SERVER = {json.dumps(base)};
__sbStart({cfg}, function (user, text, meta) {{
  var body = {{ user: user, text: text }};
  if (meta && meta.kind === "ruby") {{
    body.kind = "ruby";
    body.amount = meta.amount || 1;
    body.color = meta.color || "";
  }}
  GM_xmlhttpRequest({{
    method: "POST",
    url: SERVER + "/api/ingest",
    headers: {{ "Content-Type": "application/json" }},
    data: JSON.stringify(body)
  }});
}});
}})();
"""
        return web.Response(text=js, content_type="application/javascript")

    # ================================================================ streamers
    async def api_streamers_list(self, request: web.Request):
        self._local_only(request)
        return web.json_response({"ok": True, "streamers": self._streamer_list()})

    async def api_streamers_lookup(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        username = str(data.get("username") or "").strip().lstrip("@")
        if not username or "/" in username or " " in username:
            return web.json_response({"ok": False, "error": "invalid username"}, status=400)
        try:
            async with self._http.get(APARAT_PROFILE_API.format(username=username)) as r:
                if r.status != 200:
                    return web.json_response(
                        {"ok": False, "error": f"Aparat returned HTTP {r.status}"}, status=404)
                payload = await r.json()
        except Exception as exc:
            return web.json_response({"ok": False, "error": f"network error: {exc}"}, status=502)
        profile = (payload or {}).get("profile") or {}
        if not profile.get("username"):
            return web.json_response({"ok": False, "error": "profile not found"}, status=404)
        return web.json_response({
            "ok": True,
            "username": profile.get("username"),
            "name": profile.get("name") or profile.get("username"),
            "pic_s": profile.get("pic_s") or "",
            "pic_m": profile.get("pic_m") or "",
            "pic_b": profile.get("pic_b") or "",
        })

    async def api_streamers_add(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        username = str(data.get("username") or "").strip().lstrip("@")
        if not username:
            return web.json_response({"ok": False, "error": "missing username"}, status=400)

        display = str(data.get("display") or "").strip()
        keywords = data.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        keywords = [str(k).strip() for k in keywords if str(k).strip()]
        initial_score = int(data.get("initial_score") or 0)

        pic_url = ""
        pic_local = ""
        try:
            async with self._http.get(APARAT_PROFILE_API.format(username=username)) as r:
                if r.status == 200:
                    payload = await r.json()
                    profile = (payload or {}).get("profile") or {}
                    pic_url = profile.get("pic_s") or profile.get("pic_m") or profile.get("pic_b") or ""
                    if not display:
                        display = profile.get("name") or username
        except Exception as exc:
            log.warning("Aparat profile lookup failed for %s: %s", username, exc)

        if pic_url:
            pic_local = await self._download_pic(pic_url, username)

        st = self.index.add(
            username=username,
            display=display or username,
            keywords=keywords,
            score=initial_score,
            pic=pic_local,
            pic_url=pic_url,
        )

        if self.engine is not None and username not in self.engine.scores:
            self.engine.scores[st.username] = st.score
            self.engine.since[st.username] = st.since
            self.engine._order = None
            self.engine.emit(self.engine.board_message())

        self.emit({"type": "streamers", "streamers": self._streamer_list()})

        return web.json_response({"ok": True, "streamer": {
            "username": st.username, "display": st.display,
            "keywords": st.keywords, "score": str(st.score),
            "pic": st.pic, "pic_url": st.pic_url,
            "pic_src": f"/pics/{st.pic}" if st.pic else "",
        }})

    async def api_streamers_update(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        username = str(data.get("username") or "").strip()
        st = self.index.get(username)
        if not st:
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        if "display" in data:
            st.display = str(data["display"]).strip() or st.display
        if "keywords" in data:
            kw = data["keywords"]
            if isinstance(kw, str):
                kw = [k.strip() for k in kw.split(",") if k.strip()]
            st.keywords = [str(k).strip() for k in kw if str(k).strip()]
        if "score" in data and data["score"] is not None:
            st.score = int(data["score"])
            if self.engine is not None and username in self.engine.scores:
                self.engine.scores[username] = st.score
                self.engine._order = None
                self.engine.emit(self.engine.board_message())
        if data.get("refresh_pic"):
            pic_url = st.pic_url
            if not pic_url:
                try:
                    async with self._http.get(APARAT_PROFILE_API.format(username=username)) as r:
                        if r.status == 200:
                            payload = await r.json()
                            profile = (payload or {}).get("profile") or {}
                            pic_url = profile.get("pic_s") or ""
                except Exception:
                    pass
            if pic_url:
                local = await self._download_pic(pic_url, username)
                if local:
                    st.pic = local
                    st.pic_url = pic_url
        self.index._rebuild_aliases()
        self.index.save()
        self.emit({"type": "streamers", "streamers": self._streamer_list()})
        return web.json_response({"ok": True})

    async def api_streamers_remove(self, request: web.Request):
        self._local_only(request)
        data = await request.json()
        username = str(data.get("username") or "").strip()
        ok = self.index.remove(username)
        if ok:
            self.engine.scores.pop(username, None)
            self.engine.since.pop(username, None)
            self.engine._order = None
            try:
                for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                    p = PICS_DIR / f"{username}{ext}"
                    if p.exists():
                        p.unlink()
            except Exception:
                pass
            self.engine.emit(self.engine.board_message())
            self.emit({"type": "streamers", "streamers": self._streamer_list()})
        return web.json_response({"ok": ok})