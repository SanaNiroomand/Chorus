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


def _rows():
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue          # skip a half-written line rather than fail
    return out


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
