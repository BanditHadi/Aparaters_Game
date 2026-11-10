"""
Game engine - Streamer Boost Live (Persian lines).

Receives `ChatMessage`s, finds a streamer (by keyword), gives points,
keeps the leaderboard + top chatters, detects overtakes / new leaders /
milestones / "on fire" streaks and produces
  * overlay events   (dicts sent over the WebSocket)
  * spoken lines     (sent to the announcer)

Also tracks a **global booster**:
  - every point awarded to any streamer also fills a global counter
  - when the counter reaches `settings.booster_target` (default 50000),
    a booster is activated for `settings.booster_duration` seconds
  - while the booster is active, all points are multiplied by
    `settings.booster_multiplier` (default 2x)
  - the booster progress uses BASE points (before the multiplier), so a
    2x booster does not accelerate the next booster.
"""
from __future__ import annotations

import asyncio
import heapq
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass

from announcer import (ordinal, speakable_name, spoken_points, spoken_rubies,
                       to_fa_digits)
from chat_sources import ChatMessage
from config import STATE_FILE, Settings
from streamers import Streamer, StreamerIndex

log = logging.getLogger("engine")

TOP_CHATTERS_COUNT = 10   # تعداد نفرات برتر که به overlay فرستاده می‌شود


@dataclass
class UserStat:
    uid: str
    name: str
    avatar: str = ""
    streamer: str | None = None
    chats: int = 0
    points: int = 0
    level: int = 1
    last_vote: float = -1e9
    pending: int = 0
    pending_kind: str = ""
    pending_text: str = ""
    pending_ruby_count: int = 0
    pending_until: float = 0.0
    banned: bool = False

    def to_dict(self) -> dict:
        return {"uid": self.uid, "name": self.name, "avatar": self.avatar,
                "streamer": self.streamer, "chats": self.chats,
                "points": str(self.points), "level": self.level,
                "banned": self.banned}


@dataclass
class VoteRecord:
    code: str
    pts: int
    uid: str | None
    kind: str
    amount: str = ""
    ts: float = 0.0


ACHIEVEMENTS = {
    "first_blood": "اولین استریمری که امتیاز گرفت",
    "first_1k": "اولین استریمری که به ۱۰۰۰ امتیاز رسید",
    "first_10k": "اولین استریمری که به ۱۰۰۰۰ امتیاز رسید",
    "first_100k": "اولین استریمری که به ۱۰۰۰۰۰ امتیاز رسید",
    "ruby_fan": "اولین بیننده‌ای که ۵ روبی فرستاد",
    "chat_machine": "اولین بیننده‌ای که به ۱۰۰ چت رسید",
    "level_10": "اولین بیننده‌ای که به لِوِل ۱۰ رسید",
}


class GameEngine:
    def __init__(self, settings: Settings, index: StreamerIndex, emit, announcer):
        self.s = settings
        self.index = index
        self.emit = emit
        self.announcer = announcer

        self.scores: dict[str, int] = {}
        self.since: dict[str, int] = {}
        for s in index.all():
            self.scores[s.username] = int(s.score)
            self.since[s.username] = int(s.since or 0)
        self.seq = max(self.since.values(), default=0)
        self.users: dict[str, UserStat] = {}
        self.banned: set[str] = set()

        self._order: list[str] | None = None
        self._seen: set[str] = set()
        self._seen_q: deque[str] = deque(maxlen=5000)
        self._recent: dict[str, deque] = {}
        self._last_fire: dict[str, float] = {}
        self._top_uid: str | None = None
        self._chatters_dirty = False
        self._board_dirty = False
        self._dirty = False
        self.paused = False

        self.history: deque[VoteRecord] = deque(maxlen=50)
        self.events: deque[dict] = deque(maxlen=100)
        self.achievements: dict[str, str] = {}
        self.ruby_counts: dict[str, int] = {}

        # ---- booster state ---------------------------------------------------
        self.booster_progress: int = 0
        self.booster_until: float = 0.0

        self.stats = {"messages": 0, "votes": 0, "rubies": 0, "superchats": 0,
                      "members": 0, "started": time.time()}

    # ------------------------------------------------------------- ranking
    def ranking(self) -> list[str]:
        if self._order is None:
            self._order = sorted(self.scores, key=lambda c: (-self.scores[c], self.since.get(c, 0)))
        return self._order

    def level_for(self, chats: int) -> int:
        level = 1
        for i, need in enumerate(self.s.level_thresholds, start=1):
            if chats >= need:
                level = i
        return level

    def board_message(self) -> dict:
        # booster هم همراه با board فرستاده می‌شود تا overlay همیشه
        # وضعیت نوار بوستر را داشته باشد
        return {"type": "board",
                "board": [[c, str(self.scores[c]), self.since.get(c, 0)] for c in self.ranking()],
                **self.booster_payload()}

    def chatters_payload(self, n: int = TOP_CHATTERS_COUNT) -> list[dict]:
        top = heapq.nlargest(n, self.users.values(), key=lambda u: (u.points, u.chats))
        return [{"id": u.uid, "name": u.name, "avatar": u.avatar, "chats": u.chats,
                 "points": str(u.points), "streamer": u.streamer, "level": u.level}
                for u in top if u.points > 0]

    def pending_payload(self) -> list[dict]:
        now = time.time()
        out = []
        for u in self.users.values():
            if u.pending > 0 and now < u.pending_until:
                out.append({"uid": u.uid, "name": u.name, "points": str(u.pending),
                            "kind": u.pending_kind, "text": u.pending_text,
                            "ruby_count": u.pending_ruby_count,
                            "until": round(u.pending_until - now, 1)})
        return out

    def init_payload(self) -> dict:
        return {
            "board": self.board_message()["board"],
            "chatters": self.chatters_payload(TOP_CHATTERS_COUNT),
            **self.booster_payload(),
        }

    # ------------------------------------------------------------ booster
    def booster_payload(self) -> dict:
        now = time.monotonic()
        left = max(0.0, self.booster_until - now)
        active = left > 0
        return {
            "booster": {
                "progress": str(self.booster_progress),
                "target": str(self.s.booster_target),
                "active": active,
                "left": round(left, 3) if active else 0,
                "duration": self.s.booster_duration,
                "multiplier": self.s.booster_multiplier,
            }
        }

    def _booster_mult(self) -> int:
        if self.booster_until > time.monotonic():
            return int(self.s.booster_multiplier)
        return 1

    def _booster_add(self, pts: int) -> None:
        if self.s.booster_target <= 0:
            return
        if self.booster_until > time.monotonic():
            return
        self.booster_progress += pts
        if self.booster_progress >= self.s.booster_target:
            self._activate_booster()

    def _activate_booster(self) -> None:
        self.booster_progress = 0
        self.booster_until = time.monotonic() + self.s.booster_duration
        self._push_event({
            "kind": "booster",
            "text": f"بوستر فعال شد! امتیاز ×{self.s.booster_multiplier} برای "
                    f"{to_fa_digits(int(self.s.booster_duration))} ثانیه!",
        })
        self.emit({
            "type": "event",
            "kind": "booster",
            "text": f"بوستر فعال شد! امتیاز ×{self.s.booster_multiplier} برای "
                    f"{to_fa_digits(int(self.s.booster_duration))} ثانیه!",
            "duration": self.s.booster_duration,
            "multiplier": self.s.booster_multiplier,
        })
        # بلافاصله payload بوستر را هم بفرست
        self.emit(self.booster_payload() | {"type": "booster"})
        self.announcer.say(
            f"بوستر فعال شد! از این لحظه همه‌ی امتیازها "
            f"{to_fa_digits(self.s.booster_multiplier)} برابر می‌شن، "
            f"فقط برای {spoken_points(int(self.s.booster_duration))} ثانیه!",
            kind="booster", priority=1, ttl=45)
        log.info("Booster activated! duration=%ss mult=%sx",
                 self.s.booster_duration, self.s.booster_multiplier)

    # -------------------------------------------------------- message input
    def handle_message(self, m: ChatMessage) -> None:
        try:
            self._handle(m)
        except Exception:
            log.exception("Could not process a chat message")

    def _handle(self, m: ChatMessage) -> None:
        if self.paused:
            return
        if m.id in self._seen:
            return
        if len(self._seen_q) == self._seen_q.maxlen:
            self._seen.discard(self._seen_q[0])
        self._seen_q.append(m.id)
        self._seen.add(m.id)
        self.stats["messages"] += 1
        now = time.monotonic()

        uid = m.author_id or m.author
        if uid in self.banned:
            return

        if m.kind == "member":
            self._on_bonus(self._user(m), m, "member")
            return
        if m.kind == "ruby":
            # (currently disabled by user request)
            # self._on_ruby(self._user(m), m)
            return
        if m.kind in ("superchat", "sticker"):
            self._on_bonus(self._user(m), m, "superchat")
            return

        codes = self.index.find(m.text, self.s.max_streamers_per_message)
        if not codes:
            return
        user = self._user(m)
        if now - user.last_vote < self.s.user_cooldown:
            return
        user.last_vote = now
        for code in codes:
            self._award(code, user.level, user, "chat")
        self._claim_pending(user, codes[0])

    def _user(self, m: ChatMessage) -> UserStat:
        uid = m.author_id or m.author
        u = self.users.get(uid)
        if u is None:
            u = self.users[uid] = UserStat(uid=uid, name=m.author, avatar=m.avatar)
        else:
            u.name = m.author or u.name
            u.avatar = m.avatar or u.avatar
        return u

    # -------------------------------------------------- ruby / members
    def _on_ruby(self, user: UserStat, m: ChatMessage) -> None:
        n = max(1, int(m.ruby_count or 1))
        pts = max(self.s.ruby_min_points, n * self.s.ruby_points)
        amount = f"{to_fa_digits(n)} روبی"

        self.ruby_counts[user.uid] = self.ruby_counts.get(user.uid, 0) + 1
        if self.ruby_counts[user.uid] == 5 and "ruby_fan" not in self.achievements:
            self._grant_achievement("ruby_fan", user.uid, user.name)

        codes = self.index.find(m.text, 1)
        code = codes[0] if codes else user.streamer
        if code:
            self._award(code, pts, user, "ruby", amount=amount,
                        ruby_count=n, ruby_color=m.ruby_color)
            return

        user.pending += pts
        user.pending_kind = "ruby"
        user.pending_text = amount
        user.pending_ruby_count += n
        user.pending_until = time.time() + self.s.pending_minutes * 60
        who = speakable_name(user.name, self.s)
        self.emit({"type": "event", "kind": "notice",
                   "text": f"{user.name} جان، اسم یک استریمر رو تو چت بنویس تا {to_fa_digits(pts)} امتیاز بگیری!"})
        self.announcer.say(
            f"{who} یک هدیه‌ی روبی فرستاد! اسم یک استریمر رو تو چت بنویس تا "
            f"{spoken_points(pts)} امتیاز بگیره!",
            kind="ruby", priority=1)

    def _on_bonus(self, user: UserStat, m: ChatMessage, kind: str) -> None:
        pts = self.s.member_points * max(1, m.count)
        amount = m.amount_text or f"{m.amount_usd:.0f} دلار"
        codes = self.index.find(m.text, 1)
        code = codes[0] if codes else user.streamer
        if code:
            self._award(code, pts, user, kind, amount=amount)
            return
        user.pending += pts
        user.pending_kind = kind
        user.pending_text = amount
        user.pending_until = time.time() + self.s.pending_minutes * 60
        who = speakable_name(user.name, self.s)
        self.emit({"type": "event", "kind": "notice",
                   "text": f"{user.name} جان، اسم یک استریمر رو تو چت بنویس تا {to_fa_digits(pts)} امتیاز بگیری!"})
        what = "عضو جدید کانال شد" if kind == "member" else "سوپرچت فرستاد"
        self.announcer.say(
            f"{who} {what}! اسم یک استریمر رو تو چت بنویس تا "
            f"{spoken_points(pts)} امتیاز بگیره!",
            kind=kind, priority=1)

    def _claim_pending(self, user: UserStat, code: str) -> None:
        if user.pending > 0 and time.time() < user.pending_until:
            pts = user.pending
            kind = user.pending_kind
            text = user.pending_text
            ruby_count = user.pending_ruby_count
            user.pending = 0
            user.pending_ruby_count = 0
            user.pending_text = ""
            self._award(code, pts, user, kind, amount=text, ruby_count=ruby_count)
        elif user.pending and time.time() >= user.pending_until:
            user.pending = 0
            user.pending_ruby_count = 0
            user.pending_text = ""

    # --------------------------------------------------------------- award
    def _award(self, code: str, pts: int, user: UserStat | None, kind: str,
               amount: str = "", record: bool = True,
               ruby_count: int = 0, ruby_color: str = "") -> None:
        if code not in self.scores:
            return
        streamer = self.index.get(code)
        name = streamer.display if streamer else code

        mult = self._booster_mult()
        base_pts = pts
        actual_pts = pts * mult

        prev_order = self.ranking()
        prev_rank = prev_order.index(code) + 1
        prev_score = self.scores[code]

        level_used = user.level if user else 1
        level_up = False
        self.scores[code] = new_score = prev_score + actual_pts
        self.seq += 1
        self.since[code] = self.seq
        self._order = None
        new_rank = self.ranking().index(code) + 1

        if streamer:
            streamer.score = new_score
            streamer.since = self.seq
            self._dirty = True

        if user is not None:
            user.streamer = code
            user.points += actual_pts
            if kind == "chat":
                user.chats += 1
                new_level = self.level_for(user.chats)
                level_up = new_level > user.level
                user.level = new_level

        if record:
            self.history.append(VoteRecord(code, actual_pts, user.uid if user else None,
                                           kind, amount, time.time()))

        self._booster_add(base_pts)

        self.stats["votes"] += 1
        if kind == "ruby":
            self.stats["rubies"] += 1
        elif kind == "superchat":
            self.stats["superchats"] += 1
        elif kind == "member":
            self.stats["members"] += 1
        self._chatters_dirty = self._board_dirty = True

        if prev_score == 0 and "first_blood" not in self.achievements:
            self._grant_achievement("first_blood", code, name)
        for threshold, key in ((1000, "first_1k"), (10000, "first_10k"), (100000, "first_100k")):
            if prev_score < threshold <= new_score and key not in self.achievements:
                self._grant_achievement(key, code, name)
        if user and user.level >= 10 and "level_10" not in self.achievements:
            self._grant_achievement("level_10", user.uid, user.name)
        if user and user.chats >= 100 and "chat_machine" not in self.achievements:
            self._grant_achievement("chat_machine", user.uid, user.name)

        self.emit({
            "type": "vote", "kind": kind, "code": code, "name": name,
            "points": str(actual_pts), "base_points": str(base_pts),
            "multiplier": mult,
            "total": str(new_score),
            "rank": new_rank, "prev_rank": prev_rank,
            "seq": self.seq, "amount": amount, "level_up": level_up,
            "ruby_count": ruby_count, "ruby_color": ruby_color,
            "user": {"id": user.uid if user else "admin",
                     "name": user.name if user else "مدیر",
                     "avatar": user.avatar if user else "", "level": level_used},
            **self.booster_payload(),
        })
        self._announce(code, name, actual_pts, user, kind, amount, prev_order,
                       prev_rank, prev_score, new_rank, new_score, ruby_count)

    def _grant_achievement(self, key: str, who: str, label: str) -> None:
        self.achievements[key] = who
        title = ACHIEVEMENTS.get(key, key)
        self._push_event({"kind": "achievement",
                          "code": who if who in self.scores else None,
                          "title": title, "who": label,
                          "text": f"{label} دستاورد «{title}» رو باز کرد!"})
        self.announcer.say(f"{label} دستاورد «{title}» رو باز کرد!",
                           kind="achievement", priority=2, ttl=30)

    def _push_event(self, ev: dict) -> None:
        ev["ts"] = time.time()
        self.events.append(ev)

    # -------------------------------------------------------- announcements
    def _announce(self, code, cname, pts, user, kind, amount, prev_order,
                  prev_rank, prev_score, new_rank, new_score, ruby_count=0) -> None:
        say = self.announcer.say
        who = speakable_name(user.name, self.s) if user else "میزبان"

        if kind == "ruby":
            if ruby_count and ruby_count > 0:
                say(f"{who} {spoken_rubies(ruby_count)} برای {cname} فرستاد! "
                    f"{cname} {spoken_points(pts)} امتیاز گرفت!",
                    kind="ruby", priority=1, ttl=40)
            else:
                say(f"{who} یک هدیه‌ی روبی برای {cname} فرستاد! "
                    f"{cname} {spoken_points(pts)} امتیاز گرفت!",
                    kind="ruby", priority=1, ttl=40)
        elif kind == "superchat":
            say(f"{who} یک سوپرچت {amount} فرستاد! {cname} "
                f"{spoken_points(pts)} امتیاز گرفت!",
                kind="superchat", priority=1, ttl=40)
        elif kind == "member":
            say(f"{who} عضو جدید کانال شد! {cname} "
                f"{spoken_points(pts)} امتیاز گرفت!",
                kind="member", priority=1, ttl=40)

        if new_rank == 1 and prev_rank != 1:
            old = prev_order[0]
            if self.scores.get(old, 0) > 0:
                oname = self.index.get(old)
                oname = oname.display if oname else old
                self._push_event({"kind": "leader", "code": code, "other": old,
                                  "text": f"{cname} صدرنشین شد!"})
                self.emit({"type": "event", "kind": "leader", "code": code, "other": old,
                           "text": f"{cname} صدرنشین شد!"})
                say(f"{cname} صدرنشین شد! {oname} به رتبه‌ی دوم رفت.",
                    kind="leader", priority=2)
        elif new_rank < prev_rank and new_rank <= self.s.announce_top_n:
            passed = prev_order[new_rank - 1]
            if self.scores.get(passed, 0) > 0:
                p = self.index.get(passed)
                pname = p.display if p else passed
                self._push_event({"kind": "overtake", "code": code, "other": passed,
                                  "rank": new_rank, "text": f"{cname} از {pname} جلو زد"})
                self.emit({"type": "event", "kind": "overtake", "code": code, "other": passed,
                           "rank": new_rank, "text": f"{cname} از {pname} جلو زد"})
                say(f"{cname} از {pname} جلو زد و رفت به رتبه‌ی {ordinal(new_rank)}!",
                    kind="overtake", priority=3)

        crossed = [m for m in self.s.milestones if prev_score < m <= new_score]
        if crossed:
            m = crossed[-1]
            self._push_event({"kind": "milestone", "code": code, "value": str(m),
                              "text": f"{cname} به {to_fa_digits(f'{m:,}')} امتیاز رسید"})
            self.emit({"type": "event", "kind": "milestone", "code": code, "value": str(m),
                       "text": f"{cname} به {to_fa_digits(f'{m:,}')} امتیاز رسید"})
            say(f"{cname} به {spoken_points(m)} امتیاز رسید!",
                kind="milestone", priority=4)

        now = time.monotonic()
        dq = self._recent.setdefault(code, deque())
        dq.append((now, pts))
        while dq and now - dq[0][0] > self.s.fire_window:
            dq.popleft()
        total = sum(p for _, p in dq)
        if (kind == "chat" and total >= self.s.fire_points
                and now - self._last_fire.get(code, -1e9) > self.s.fire_cooldown):
            self._last_fire[code] = now
            self._push_event({"kind": "fire", "code": code, "value": str(total),
                              "text": f"{cname} آتیش گرفت!"})
            self.emit({"type": "event", "kind": "fire", "code": code, "value": str(total),
                       "text": f"{cname} آتیش گرفت!"})
            say(f"{cname} آتیش گرفت! {spoken_points(total)} امتیاز فقط تو "
                f"{to_fa_digits(int(self.s.fire_window))} ثانیه!",
                kind="fire", priority=4)

        if prev_score == 0 and kind == "chat":
            active = sum(1 for v in self.scores.values() if v > 0)
            if active <= 12:
                say(f"{cname} وارد بازی شد!", kind="join", priority=7, ttl=10)

        if user is not None:
            self._check_top_chatter(user)

    def _check_top_chatter(self, user: UserStat) -> None:
        if self._top_uid is None:
            self._top_uid = user.uid
            return
        if user.uid == self._top_uid:
            return
        current = self.users.get(self._top_uid)
        if current is None or user.points > current.points:
            self._top_uid = user.uid
            if user.points >= 10:
                who = speakable_name(user.name, self.s)
                self._push_event({"kind": "chatter",
                                  "text": f"{user.name} نفر اول چت شد!"})
                self.emit({"type": "event", "kind": "chatter",
                           "text": f"{user.name} نفر اول چت شد!"})
                self.announcer.say(
                    f"{who} حالا نفر اولِ چت هست با "
                    f"{spoken_points(user.points)} امتیاز!", kind="chatter", priority=3)

    # ----------------------------------------------------- admin operations
    def admin_add(self, code: str, pts: int) -> bool:
        if code not in self.scores or pts == 0:
            return False
        if pts < 0:
            self.scores[code] = max(0, self.scores[code] + pts)
            self.seq += 1
            self.since[code] = self.seq
            st = self.index.get(code)
            if st:
                st.score = self.scores[code]; st.since = self.seq
            self._order = None
            self._dirty = self._board_dirty = True
            self.index.save()
            self.emit(self.board_message())
            return True
        self._award(code, pts, None, "chat")
        return True

    def admin_set_points(self, code: str, pts: int) -> bool:
        if code not in self.scores:
            return False
        delta = pts - self.scores[code]
        if delta == 0:
            return True
        return self.admin_add(code, delta)

    def admin_set_user_points(self, uid: str, pts: int) -> bool:
        u = self.users.get(uid)
        if not u:
            return False
        u.points = max(0, int(pts))
        self._chatters_dirty = True
        self._dirty = True
        self.emit({"type": "chatters", "list": self.chatters_payload(TOP_CHATTERS_COUNT)})
        return True

    def admin_ban(self, uid: str, banned: bool = True) -> bool:
        u = self.users.get(uid)
        if not u:
            return False
        u.banned = banned
        if banned:
            self.banned.add(uid)
        else:
            self.banned.discard(uid)
        self._dirty = True
        return True

    def admin_pause(self, paused: bool) -> bool:
        self.paused = bool(paused)
        self.emit({"type": "event", "kind": "notice",
                   "text": "بازی متوقف شد" if paused else "بازی ادامه پیدا کرد"})
        return True

    def admin_undo(self) -> bool:
        if not self.history:
            return False
        rec = self.history.pop()
        self.scores[rec.code] = max(0, self.scores[rec.code] - rec.pts)
        self.seq += 1
        self.since[rec.code] = self.seq
        st = self.index.get(rec.code)
        if st:
            st.score = self.scores[rec.code]; st.since = self.seq
        self._order = None
        if rec.uid and rec.uid in self.users:
            u = self.users[rec.uid]
            u.points = max(0, u.points - rec.pts)
            if rec.kind == "chat":
                u.chats = max(0, u.chats - 1)
                u.level = self.level_for(u.chats)
        self._dirty = self._board_dirty = self._chatters_dirty = True
        self.index.save()
        self.emit(self.board_message())
        self.emit({"type": "chatters", "list": self.chatters_payload(TOP_CHATTERS_COUNT)})
        return True

    def admin_clear_queue(self) -> None:
        self.announcer.clear_queue()

    def admin_confirm_ruby(self, user_name: str, code: str, n: int) -> bool:
        if code not in self.scores:
            return False
        uid = f"manual:{user_name}"
        u = self.users.get(uid) or self.users.setdefault(
            uid, UserStat(uid=uid, name=user_name))
        pts = max(self.s.ruby_min_points, int(n) * self.s.ruby_points)
        self._award(code, pts, u, "ruby", amount=f"{to_fa_digits(n)} روبی", ruby_count=int(n))
        return True

    def admin_clear_achievements(self) -> None:
        self.achievements.clear()
        self._dirty = True

    def admin_booster(self, action: str) -> bool:
        if action == "start":
            self._activate_booster()
            return True
        if action == "reset":
            self.booster_progress = 0
            self.booster_until = 0.0
            self.emit(self.booster_payload() | {"type": "booster"})
            return True
        return False

    def events_payload(self, n: int = 50) -> list[dict]:
        return list(self.events)[-n:]

    def history_payload(self, n: int = 20) -> list[dict]:
        out = []
        for r in list(self.history)[-n:][::-1]:
            u = self.users.get(r.uid) if r.uid else None
            st = self.index.get(r.code)
            out.append({"code": r.code,
                        "name": st.display if st else r.code,
                        "pts": str(r.pts), "kind": r.kind,
                        "amount": r.amount, "user": u.name if u else "مدیر",
                        "ts": r.ts})
        return out

    def state_payload(self) -> dict:
        e = self
        top = []
        for c in e.ranking()[:20]:
            st = self.index.get(c)
            top.append({"code": c, "name": st.display if st else c,
                        "username": c, "points": str(e.scores[c])})
        zero = []
        for c in e.ranking():
            if e.scores[c] == 0:
                st = self.index.get(c)
                zero.append({"code": c, "name": st.display if st else c})
        return {
            "stats": e.stats,
            "top_countries": top,
            "top_chatters": e.chatters_payload(TOP_CHATTERS_COUNT),
            "pending": e.pending_payload(),
            "history": e.history_payload(20),
            "events": e.events_payload(50),
            "zero_countries": zero,
            "achievements": [{"key": k, "who": v, "title": ACHIEVEMENTS.get(k, k)}
                             for k, v in e.achievements.items()],
            "banned": list(e.banned),
            "paused": e.paused,
            **self.booster_payload(),
        }

    def reset(self) -> None:
        for c in self.scores:
            self.scores[c] = 0
        for i, s in enumerate(sorted(self.index.all(), key=lambda x: x.username)):
            s.score = 0
            s.since = i + 1
        self.since = {s.username: s.since for s in self.index.all()}
        self.seq = max(self.since.values(), default=0)
        self.users.clear()
        self.banned.clear()
        self._recent.clear()
        self._top_uid = None
        self._order = None
        self.history.clear()
        self.events.clear()
        self.achievements.clear()
        self.ruby_counts.clear()
        self.booster_progress = 0
        self.booster_until = 0.0
        self._dirty = True
        self.index.save()
        self.emit({"type": "reset"})
        self.emit(self.board_message())
        self.emit({"type": "chatters", "list": []})
        self.emit(self.booster_payload() | {"type": "booster"})

    # ---------------------------------------------------------- persistence
    def save(self) -> None:
        try:
            data = {
                "users": [u.to_dict() for u in self.users.values() if u.points > 0],
                "banned": list(self.banned),
                "achievements": self.achievements,
                "ruby_counts": self.ruby_counts,
                "booster_progress": str(self.booster_progress),
            }
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
            self.index.save()
            self._dirty = False
        except OSError as exc:
            log.warning("Could not save state: %s", exc)

    def load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for u in data.get("users", []):
                self.users[u["uid"]] = UserStat(
                    uid=u["uid"], name=u.get("name", "بیننده"), avatar=u.get("avatar", ""),
                    streamer=u.get("streamer") or u.get("country"),
                    chats=int(u.get("chats", 0)),
                    points=int(u.get("points", 0)), level=int(u.get("level", 1)),
                    banned=bool(u.get("banned", False)))
            self.banned = set(data.get("banned", []))
            self.achievements = dict(data.get("achievements", {}))
            self.ruby_counts = dict(data.get("ruby_counts", {}))
            self.booster_progress = int(data.get("booster_progress", 0))
            if self.users:
                self._top_uid = max(self.users.values(), key=lambda x: x.points).uid
            self._order = None
            log.info("Restored game: %d streamers, %d viewers, booster %d/%d",
                     len(self.scores), len(self.users),
                     self.booster_progress, self.s.booster_target)
        except Exception as exc:
            log.warning("Could not load the saved state (%s) - starting fresh", exc)

    # -------------------------------------------------------- background loop
    async def run(self) -> None:
        now = time.monotonic()
        last_board = last_save = now
        s = self.s
        next_standings = now + s.standings_minutes * 60 if s.standings_minutes > 0 else None
        next_reminder = now + s.reminder_minutes * 60 + 45 if s.reminder_minutes > 0 else None
        last_booster_broadcast = now
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if self._chatters_dirty:
                self._chatters_dirty = False
                self.emit({"type": "chatters", "list": self.chatters_payload(TOP_CHATTERS_COUNT)})
            if self._board_dirty and now - last_board >= 3:
                self._board_dirty = False
                last_board = now
                # board حالا booster را هم شامل می‌شود
                self.emit(self.board_message())
            if self._dirty and now - last_save >= 10:
                last_save = now
                self.save()
            # هر ۱ ثانیه یک بار وضعیت بوستر را بفرست (چه فعال چه در حال پر شدن)
            if now - last_booster_broadcast >= 1.0:
                last_booster_broadcast = now
                self.emit(self.booster_payload() | {"type": "booster"})
            if next_standings and now >= next_standings:
                next_standings = now + s.standings_minutes * 60
                self._announce_standings()
            if next_reminder and now >= next_reminder:
                next_reminder = now + s.reminder_minutes * 60
                self.announcer.say(
                    "اسم استریمر مورد علاقه‌ات رو تو چت بنویس تا امتیاز بگیره! "
                    f"هر روبی {to_fa_digits(s.ruby_points)} امتیاز داره، "
                    f"و هر عضو جدید {to_fa_digits(s.member_points)} امتیاز می‌گیره!",
                    kind="reminder", priority=8, ttl=30)

    def _announce_standings(self) -> None:
        top = self.ranking()[:3]
        if not top or self.scores[top[0]] < 50:
            return
        names = []
        pts = []
        for c in top:
            st = self.index.get(c)
            names.append(st.display if st else c)
            pts.append(spoken_points(self.scores[c]))
        self.announcer.say(
            f"وضعیت جدول: نفر اول {names[0]} با {pts[0]} امتیاز. "
            f"نفر دوم {names[1]} با {pts[1]} امتیاز. "
            f"و نفر سوم {names[2]} با {pts[2]} امتیاز.",
            kind="standings", priority=6, ttl=30)