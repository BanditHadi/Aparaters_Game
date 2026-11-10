#!/usr/bin/env python3
"""
Streamer Boost Live - Aparat live-chat streamer battle game.

  python main.py --aparat-url https://www.aparat.com/NAME/live
  python main.py --source demo
  python main.py --source youtube --video <VIDEO_ID>     # optional
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from announcer import Announcer
from chat_sources import make_source
from config import (AUDIO_DIR, BROWSER_DIR, DATA_DIR, PICS_DIR,
                    STREAMERS_FILE, Settings, load_dotenv)
from game_engine import GameEngine
from server import OverlayServer
from streamers import StreamerIndex

log = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live chat streamer battle game (Aparat by default)")
    p.add_argument("--source", choices=["aparat", "demo", "youtube", "off"],
                   help="where to read the chat from (default: aparat)")
    p.add_argument("--aparat-url", help="Aparat live page URL")
    p.add_argument("--aparat-mode", choices=["dom", "ws"], help="Aparat read mode")
    p.add_argument("--aparat-headful", action="store_true",
                   help="Show the Chromium window")
    p.add_argument("--aparat-debug", action="store_true", help="Log every raw Aparat message")
    p.add_argument("-v", "--video", help="YouTube live video ID or URL (optional)")
    p.add_argument("--backend", choices=["pytchat", "api"], help="YouTube backend")
    p.add_argument("--api-key", help="YouTube Data API key")
    p.add_argument("--demo", action="store_true", help="Use fake viewers")
    p.add_argument("--port", type=int, help="web server port (default 8080)")
    p.add_argument("--host", help="bind address (default 0.0.0.0)")
    p.add_argument("--tts", choices=["edge", "pyttsx3", "browser", "off"], help="voice engine")
    p.add_argument("--voice", help="edge-tts voice name for English")
    p.add_argument("--voice-fa", help="edge-tts voice name for Persian")
    p.add_argument("--language", choices=["en", "fa"], help="announcer language")
    p.add_argument("--fresh", action="store_true", help="ignore the saved game")
    return p.parse_args()


def build_settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if args.source:
        s.source = args.source
    if args.aparat_url:
        s.aparat_channel_url = args.aparat_url
        s.source = "aparat"
    if args.aparat_mode:
        s.aparat_mode = args.aparat_mode
    if args.aparat_headful:
        s.aparat_headless = False
    if args.aparat_debug:
        s.aparat_debug = True
    if args.video:
        s.video = args.video
        s.source = "youtube"
    if args.backend:
        s.backend = args.backend
    if args.api_key:
        s.api_key = args.api_key
        if not args.backend:
            s.backend = "api"
    if args.demo:
        s.demo = True
        s.source = "demo"
    if args.port:
        s.port = args.port
    if args.host:
        s.host = args.host
    if args.tts:
        s.tts_engine = args.tts
    if args.voice:
        s.tts_voice = args.voice
    if args.voice_fa:
        s.tts_voice_fa = args.voice_fa
    if args.language:
        s.language = args.language
    return s


async def amain(settings: Settings, fresh: bool) -> None:
    for d in (DATA_DIR, AUDIO_DIR, PICS_DIR, BROWSER_DIR):
        d.mkdir(parents=True, exist_ok=True)

    if settings.source == "aparat" and not settings.aparat_channel_url:
        print("\n" + "!" * 62)
        print("  ERROR: source=aparat but no Aparat URL was given.")
        print()
        print("  Run like this:")
        print("    python main.py --aparat-url https://www.aparat.com/NAME/live")
        print()
        print("  Or set it in your .env:")
        print("    APARAT_URL=https://www.aparat.com/NAME/live")
        print()
        print("  If you just want to test offline, run:")
        print("    python main.py --source demo")
        print("!" * 62 + "\n")
        return

    index = StreamerIndex(STREAMERS_FILE)
    if not index.all():
        print("\n" + "=" * 62)
        print("  No streamers registered yet.")
        print("  Open the admin panel and click «+ Add streamer»,")
        print("  or go directly to:  http://localhost:%d/add" % settings.port)
        print("=" * 62 + "\n")

    server = OverlayServer(settings, index)
    announcer = Announcer(settings, server.emit, server.has_clients)
    engine = GameEngine(settings, index, server.emit, announcer)

    current_source_task: dict[str, asyncio.Task | None] = {"task": None}

    async def reload_sources() -> None:
        task = current_source_task["task"]
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        source = make_source(settings, index)
        current_source_task["task"] = asyncio.create_task(
            source.run(engine.handle_message), name="chat-source")
        log.info("Chat source reloaded: %s", settings.source)

    server.bind(engine, announcer, reload_sources)
    if not fresh:
        engine.load()
    source = make_source(settings, index)

    runner = await server.start()
    shown_host = "localhost" if settings.host in ("0.0.0.0", "::") else settings.host
    print("\n" + "=" * 62)
    print("  STREAMER BOOST LIVE is running")
    print(f"  Chat source                  : {settings.source}")
    if settings.source == "aparat":
        print(f"  Aparat URL                   : {settings.aparat_channel_url}")
        print(f"  Aparat mode                  : {settings.aparat_mode}"
              f"{' (headless)' if settings.aparat_headless else ' (visible window)'}")
    elif settings.source == "youtube":
        print(f"  YouTube video                : {settings.video}")
    print(f"  Streamers registered         : {len(index.all())}")
    print(f"  Overlay (OBS Browser Source) : http://{shown_host}:{settings.port}/")
    print(f"  Control panel                : http://{shown_host}:{settings.port}/admin")
    print(f"  Add streamer panel           : http://{shown_host}:{settings.port}/add")
    print(f"  Aparat userscript            : http://{shown_host}:{settings.port}/userscript.user.js")
    print("  Recommended OBS size         : 1080 x 1920 (vertical) or any 9:16 size")
    print("=" * 62 + "\n")

    current_source_task["task"] = asyncio.create_task(
        source.run(engine.handle_message), name="chat-source")

    tasks = [
        asyncio.create_task(engine.run(), name="engine"),
        asyncio.create_task(announcer.run(), name="announcer"),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        if current_source_task["task"]:
            current_source_task["task"].cancel()
        engine.save()
        await server.stop()
        await runner.cleanup()


def main() -> None:
    load_dotenv()
    args = parse_args()
    settings = build_settings(args)
    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s | %(levelname)-7s | %(name)-9s | %(message)s")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required.")
    try:
        asyncio.run(amain(settings, args.fresh))
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()