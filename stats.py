"""
stats.py — usage tracking for the Chorus bot.

Appends one JSON line per event to a log file and can summarise it for the
/stats command. Deliberately simple: no database, no dependencies.

Chat ids are stored as a short hash, so the log answers "how many people" and
"how often" without keeping identifiers for anyone.

Set CHORUS_DATA_DIR to a path that survives redeploys. On a container host the
working directory is usually wiped on each deploy, which would silently reset
the numbers.

Nothing here is allowed to break the bot: every entry point swallows its own
errors, because losing a statistic matters far less than dropping a message.
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

DATA_DIR = os.getenv("CHORUS_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(DATA_DIR, "usage.jsonl")
USERS_PATH = os.path.join(DATA_DIR, "users.jsonl")


def _user_key(chat_id):
    """Short stable hash, so users can be counted but not identified."""
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:12]


def record(event, chat_id, **fields):
    """Append one event. Never raises."""
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "user": _user_key(chat_id),
        }
        row.update(fields)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue          # skip a half-written line rather than fail
    return out


def _rows():
    return _read(LOG_PATH)


_known = None


def seen(chat_id, who):
    """Note a visitor the first time they appear. Never raises.

    Unlike the usage log, this keeps real identities: the chat id, and the
    username and name Telegram supplies. That is what makes it possible to
    answer "who has used this", so treat the file as personal data.
    """
    global _known
    try:
        if _known is None:
            _known = {r.get("chat_id") for r in _read(USERS_PATH)}
        if chat_id in _known:
            return
        _known.add(chat_id)
        who = who or {}
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chat_id": chat_id,
            "user": _user_key(chat_id),      # links to the usage log
            "username": who.get("username"),
            "name": " ".join(p for p in [who.get("first_name"),
                                         who.get("last_name")] if p) or None,
            "lang": who.get("language_code"),
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USERS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def visitors():
    """Everyone who has opened the bot, newest first, with their activity."""
    people = {}
    for r in _read(USERS_PATH):           # later rows win if a name changed
        if r.get("chat_id") is not None:
            people[r["chat_id"]] = r

    built = Counter()
    last_seen = {}
    for r in _rows():
        key = r.get("user")
        if not key:
            continue
        if r.get("event") == "built":
            built[key] += 1
        if r.get("ts") and (key not in last_seen or r["ts"] > last_seen[key]):
            last_seen[key] = r["ts"]

    out = []
    for chat_id, p in people.items():
        key = p.get("user")
        out.append({
            "chat_id": chat_id,
            "username": p.get("username"),
            "name": p.get("name"),
            "lang": p.get("lang"),
            "first_seen": p.get("ts", ""),
            "last_active": last_seen.get(key, ""),
            "exercises": built.get(key, 0),
        })
    out.sort(key=lambda p: p["first_seen"], reverse=True)
    return out


def visitors_summary(limit=30):
    """Human-readable HTML list of who has opened the bot."""
    people = visitors()
    if not people:
        return ("Nobody recorded yet.\n\nLog file: <code>{}</code>\n"
                "It fills up as people open the bot.".format(USERS_PATH))

    lines = ["👥 <b>Visitors</b> — {} total".format(len(people)), ""]
    for p in people[:limit]:
        who = p["name"] or "unnamed"
        if p["username"]:
            who += " (@{})".format(p["username"])
        lines.append("• <b>{}</b>".format(who))
        lines.append("   id <code>{}</code>{} · joined {} · {} exercise{}".format(
            p["chat_id"],
            " · " + p["lang"] if p["lang"] else "",
            (p["first_seen"] or "?")[:10],
            p["exercises"], "" if p["exercises"] == 1 else "s"))
    if len(people) > limit:
        lines.append("")
        lines.append("<i>…and {} more.</i>".format(len(people) - limit))
    return "\n".join(lines)


def summary():
    """Human-readable HTML summary of everything recorded so far."""
    rows = _rows()
    if not rows:
        return ("No usage recorded yet.\n\nLog file: <code>{}</code>\n"
                "It appears once someone builds an exercise.".format(LOG_PATH))

    built = [r for r in rows if r.get("event") == "built"]
    graded = [r for r in rows if r.get("event") == "graded"]
    users = {r.get("user") for r in rows if r.get("user")}

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent = 0
    for r in built:
        try:
            if datetime.fromisoformat(r["ts"]) >= week_ago:
                recent += 1
        except (ValueError, KeyError):
            pass

    lines = [
        "📊 <b>Chorus usage</b>",
        "",
        "👤 People: <b>{}</b>".format(len(users)),
        "🎧 Exercises built: <b>{}</b>  (last 7 days: {})".format(len(built), recent),
        "✍️ Exercises finished: <b>{}</b>".format(len(graded)),
    ]

    scored = [(r.get("correct", 0), r.get("total", 0)) for r in graded if r.get("total")]
    if scored:
        got = sum(c for c, _ in scored)
        outof = sum(t for _, t in scored)
        lines.append("🎯 Average score: <b>{}%</b>  ({}/{})".format(
            round(got / outof * 100), got, outof))

    levels = Counter(r.get("level") for r in built if r.get("level"))
    if levels:
        lines += ["", "<b>Levels</b>"]
        lines += ["  {} — {}".format(lvl, n) for lvl, n in levels.most_common()]

    songs = Counter(r.get("song") for r in built if r.get("song"))
    if songs:
        lines += ["", "<b>Most practised</b>"]
        lines += ["  {} × {}".format(n, song) for song, n in songs.most_common(5)]

    finished = (len(graded) / len(built) * 100) if built else 0
    lines += ["", "<i>{}% of exercises get finished.</i>".format(round(finished))]
    return "\n".join(lines)
