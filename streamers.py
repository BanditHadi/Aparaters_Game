"""
Streamer detection.

A chat message is scanned for the keywords of every registered streamer.
The streamer's display name is ALWAYS a keyword (in addition to the custom
keywords list).

Data model:
    {
        "username":   "bandit.hadi",           # Aparat username (unique id)
        "display":    "Bandit",                # shown under the profile pic
        "keywords":   ["بندیت", "bandit"],     # case-insensitive
        "score":      0,                       # current score
        "since":      0,                       # sequence number for tie-breaks
        "pic":        "bandit.hadi.jpg",       # local file name in data/pics/
        "pic_url":    "https://.../....jpg",   # original URL (informational)
        "added":      1234567890.0             # unix timestamp
    }
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("streamers")


# --------------------------------------------------------------------------- #
# Text normalisation
# --------------------------------------------------------------------------- #
_LETTER_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ە": "ه", "ھ": "ه", "ہ": "ه",
    "ٱ": "ا", "ۓ": "ی",
})
_STRIP_RE = re.compile("[\u0640\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_ZW_RE = re.compile("[\u200c\u200d]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_LETTER_MAP)
    text = _STRIP_RE.sub("", text)
    text = _ZW_RE.sub(" ", text)
    return text.casefold()


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            cur.append(ch)
        elif cur:
            tokens.append("".join(cur))
            cur = []
    if cur:
        tokens.append("".join(cur))
    return tokens


def _key(text: str) -> str:
    return " ".join(normalize(t) for t in _tokenize(text))


# --------------------------------------------------------------------------- #
# Streamer
# --------------------------------------------------------------------------- #
@dataclass
class Streamer:
    username: str
    display: str
    keywords: list[str] = field(default_factory=list)
    score: int = 0
    since: int = 0
    pic: str = ""
    pic_url: str = ""
    added: float = 0.0

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "display": self.display,
            "keywords": list(self.keywords),
            "score": str(self.score),
            "since": self.since,
            "pic": self.pic,
            "pic_url": self.pic_url,
            "added": self.added,
        }


# --------------------------------------------------------------------------- #
# StreamerIndex
# --------------------------------------------------------------------------- #
class StreamerIndex:
    def __init__(self, path: Path):
        self.path = path
        self.streamers: dict[str, Streamer] = {}
        self._alias: dict[str, str] = {}     # normalized keyword -> username
        self._max_n = 1
        self._load()

    # ----------------------------------------------------------------- load
    def _load(self) -> None:
        if not self.path.exists():
            log.info("No streamers.json yet - add streamers from the /add panel")
            return
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s: %s", self.path, exc)
            return
        for r in rows:
            try:
                s = Streamer(
                    username=str(r["username"]).strip(),
                    display=str(r.get("display") or r["username"]).strip(),
                    keywords=[str(k).strip() for k in r.get("keywords", []) if str(k).strip()],
                    score=int(r.get("score", 0)),
                    since=int(r.get("since", 0)),
                    pic=str(r.get("pic", "")),
                    pic_url=str(r.get("pic_url", "")),
                    added=float(r.get("added", 0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping bad streamer row %r: %s", r, exc)
                continue
            self.streamers[s.username] = s
        self._rebuild_aliases()
        log.info("Loaded %d streamers", len(self.streamers))

    def _rebuild_aliases(self) -> None:
        self._alias.clear()
        self._max_n = 1
        for s in self.streamers.values():
            keys = set(s.keywords)
            if s.display:
                keys.add(s.display)
            for k in keys:
                nk = _key(k)
                if len(nk) < 2:
                    continue
                self._alias.setdefault(nk, s.username)
                self._max_n = max(self._max_n, min(8, nk.count(" ") + 1))

    # ----------------------------------------------------------------- save
    def save(self) -> None:
        rows = [s.to_dict() for s in self.streamers.values()]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ api
    def get(self, username: str) -> Streamer | None:
        return self.streamers.get(username)

    def all(self) -> list[Streamer]:
        return list(self.streamers.values())

    def find(self, text: str, limit: int = 1) -> list[str]:
        """Return up to `limit` distinct streamer usernames mentioned in `text`."""
        if not text:
            return []
        found: list[str] = []

        def add(u: str | None) -> None:
            if u and u not in found:
                found.append(u)

        tokens = [_key(t) for t in _tokenize(text)]
        i, total = 0, len(tokens)
        while i < total and len(found) < limit:
            hit = None
            for n in range(min(self._max_n, total - i), 0, -1):
                u = self._alias.get(" ".join(tokens[i:i + n]))
                if u:
                    hit = (u, n)
                    break
            if hit:
                add(hit[0])
                i += hit[1]
            else:
                i += 1
        return found[:limit]

    # ----------------------------------------------------------------- admin
    def add(self, username: str, display: str, keywords: list[str],
            score: int = 0, pic: str = "", pic_url: str = "") -> Streamer:
        u = username.strip()
        s = self.streamers.get(u)
        if s is None:
            s = Streamer(username=u, display=display or u, added=__import__("time").time())
            self.streamers[u] = s
        s.display = display or s.display or u
        s.keywords = list(dict.fromkeys([k.strip() for k in keywords if k.strip()]))
        s.score = int(score)
        s.pic = pic or s.pic
        s.pic_url = pic_url or s.pic_url
        if s.since == 0:
            s.since = max((x.since for x in self.streamers.values()), default=0) + 1
        self._rebuild_aliases()
        self.save()
        return s

    def remove(self, username: str) -> bool:
        if username not in self.streamers:
            return False
        del self.streamers[username]
        self._rebuild_aliases()
        self.save()
        return True

    def reset_scores(self) -> None:
        for s in self.streamers.values():
            s.score = 0
        for i, s in enumerate(sorted(self.streamers.values(), key=lambda x: x.username)):
            s.since = i + 1
        self.save()

    # ------------------------------------------------------------------ voters
    def voter_pic_filename(self, username: str) -> str:
        """Return the local file name of a voter's picture, if already cached.

        Voter pictures are stored as `data/pics/v_<username>.<ext>`.
        """
        from config import PICS_DIR
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", username or "")[:60]
        if not safe:
            return ""
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            p = PICS_DIR / f"v_{safe}{ext}"
            if p.exists() and p.stat().st_size > 200:
                return f"v_{safe}{ext}"
        return ""