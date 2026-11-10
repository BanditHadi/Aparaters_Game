# 🌍 Country Boost Live

A **YouTube live-chat game written in Python**. Viewers type a country name (`Iran`, `IR`, `🇮🇷`, `ایران` …) in the live chat and that country gets points. The leaderboard is an animated, vertical overlay (9:16) that you add to OBS as a *Browser Source*, and an **English voice announcer** commentates the battle:

> "Iran passes Indonesia and moves into second place!" · "AlbCro22 is now the number one top chatter!" · "Ali sent a $10 Super Chat! Brazil receives 1000 points!"

Everything on screen and in the voice is **English**, while Persian / Arabic text (viewer names and country names typed in chat) is fully supported (Vazirmatn / Noto Sans Arabic fonts, RTL aware).

---

## ✨ Features

| | |
|---|---|
| **Chat reader** | `pytchat` (no API key, no quota) or the official YouTube Data API v3 |
| **All countries + flags** | 240+ countries/territories, flag images are downloaded once and cached locally |
| **Smart matching** | English names, ISO codes (`IR`, `IRN`), aliases (`USA`, `UK`, `UAE`, `Holland`), flag emojis, and names in Persian, Arabic, Turkish, Russian, Spanish, French, German, Chinese, Japanese, Korean … (via Babel/CLDR). Short codes have safety rules so `in`, `it`, `no`, `me` don't score by accident |
| **Levels** | Every valid vote raises the viewer's level: Level 1 = +1 point … Level 10 = +10 points |
| **Super Chat** | `$1 = 100 points` (configurable, any currency). The country is taken from the Super Chat text, or from the viewer's last country. No country yet? The bonus waits for the viewer's next country message |
| **Members** | New member / gifted memberships = +400 points each |
| **Voice announcer** | Overtakes, new leader, top chatter changes, Super Chats, members, milestones (1,000 / 10,000 …), "on fire" streaks, periodic standings and reminders. Priority queue + rate limits so it never spams. Engines: `edge-tts` (neural voices), `pyttsx3` (offline), browser voice, or off |
| **Animations** | Smooth rank reordering, floating `+N` points, ripples, count-up numbers, medals + crown, ▲ trends, 🔥 on-fire flags, banners, full-screen Super Chat splash with confetti + screen shake, animated Top-5 chatters, sound effects (synthesised, no files) |
| **Control panel** | `/admin`: simulate viewers/Super Chats, make the announcer talk, mute, adjust points, reset |
| **Persistence** | Scores and viewers are saved to `data/state.json` and restored on restart |
| **Demo mode** | `--demo` produces fake viewers; `index.html?demo=1` previews the overlay with no Python at all |

---

## 🚀 Quick start

```bash
cd country_boost
python -m venv venv
# Windows:  venv\Scripts\activate        Linux/macOS:  source venv/bin/activate
pip install -r requirements.txt

python main.py --demo                       # try it with fake viewers
python main.py --video <VIDEO_ID_or_URL>    # real YouTube live stream
```

Windows users can simply double-click **`run_windows.bat`** (Linux/macOS: `bash run_linux_mac.sh`). Optionally copy `env.example` to `.env` to keep your settings.

Then open:

* **Overlay**: <http://localhost:8080/>
* **Control panel**: <http://localhost:8080/admin>

### Add it to OBS

1. *Sources → + → Browser*
2. URL `http://localhost:8080/`, size **1080 × 1920** (any 9:16 size works; the stage scales automatically)
3. Tick **"Control audio via OBS"** so the announcer voice is mixed into your stream.
4. Optional URL parameters:

| Parameter | Meaning |
|---|---|
| `?bg=transparent` | transparent background (put it over your own video) |
| `?cols=9` | flags per row (4 – 14) |
| `?sfx=0` | disable sound effects |
| `?voice=0` | do not play the announcer voice in this overlay |
| `?volume=0.7` | sound effect volume |

> In a normal browser (not OBS) click the *"Click once to enable sound"* button — browsers block audio until you interact with the page.

### Reading the chat

```bash
# 1) pytchat (default) - works without any key
python main.py --video https://www.youtube.com/watch?v=XXXXXXXXXXX

# 2) official API - needs a YouTube Data API v3 key
python main.py --video XXXXXXXXXXX --backend api --api-key YOUR_KEY
```

The official API has a daily quota (≈ 10 000 units; every poll costs ~5), so `pytchat` is recommended for long streams.

---

## ⚙️ Configuration

Edit `config.py`, or copy `env.example` to `.env`, or use flags (`python main.py --help`).

| Setting | Default | What it does |
|---|---|---|
| `level_thresholds` | `0,5,15,35,70,120,200,320,500,800` | votes needed for level 1 … 10 |
| `user_cooldown` | `2` s | min time between two votes of one viewer |
| `max_countries_per_message` | `1` | countries counted per message |
| `superchat_points_per_usd` | `100` | Super Chat value |
| `member_points` | `400` | per new member |
| `tts_engine` | `edge` | `edge` · `pyttsx3` · `browser` · `off` |
| `tts_voice` | `en-US-AndrewMultilingualNeural` | any edge-tts voice (`python -m edge_tts --list-voices`) |
| `non_latin_names` | `skip` | Persian/Arabic names are replaced by "a viewer" in speech |
| `name_languages` | fa, ar, tr, ru, es, fr, de, pt, id, ur, hi, zh, ja, ko, th | languages whose country names are recognised |

Add your own aliases in `countries.py → EXTRA_ALIASES`.

Test the matcher without YouTube: `python countries.py`.

---

## 🧱 Project layout

```
country_boost/
├── main.py            entry point (CLI, wiring)
├── config.py          settings, currency table
├── countries.py       country matcher (names, codes, emojis, Persian/Arabic ...)
├── chat_sources.py    pytchat / YouTube API / demo chat
├── game_engine.py     scoring, levels, ranking, events, persistence
├── announcer.py       voice queue + TTS engines
├── server.py          aiohttp server: overlay, WebSocket, flag cache, control API
├── data/countries.json
└── static/            index.html · style.css · app.js · demo.js · admin.html
```

**Data flow:** YouTube chat → `chat_sources` → `game_engine` (matcher + scoring) → events → `server` (WebSocket) → overlay animations, and → `announcer` → speech clip → overlay plays it.

---

## 🛠️ Troubleshooting

* **Nothing happens / "not live yet"** – the stream must be live (or a premiere) with chat enabled. pytchat retries automatically.
* **No voice** – install `edge-tts` (needs internet), check OBS "Control audio via OBS", or try `--tts browser` / `--tts pyttsx3`.
* **Flags are missing** – the first run downloads flag images from flagcdn.com into `cache/flags/`; keep internet on once.
* **Unknown Super Chat currency** – add it to `USD_RATES` in `config.py`.
* **Start over** – `python main.py --fresh` or the *Reset game* button in `/admin`.

---

## 🇮🇷 راهنمای کوتاه فارسی

1. پایتون ۳.۱۰ یا بالاتر نصب کنید و داخل پوشه‌ی پروژه `pip install -r requirements.txt` را بزنید.
2. برای تست: `python main.py --demo`
3. برای لایو واقعی: `python main.py --video لینک_یا_آیدی_ویدیو`
4. در OBS یک **Browser Source** با آدرس `http://localhost:8080/` و اندازه‌ی ۱۰۸۰×۱۹۲۰ اضافه کنید و گزینه‌ی *Control audio via OBS* را فعال کنید تا صدای گوینده در استریم پخش شود.
5. بینندگان نام کشور را (مثلاً `Iran`، `IR`، `ایران` یا ایموجی پرچم) در چت می‌نویسند و برای کشور خود امتیاز می‌گیرند. سوپرچت و ممبر امتیاز بیشتری دارند.
6. تمام متن‌های بازی انگلیسی است، اما نام و پیام‌های فارسی و عربی (با فونت Vazirmatn) درست نمایش داده می‌شوند.
