"""
exercise.py — Chorus's shared core.

Given an artist + title it:
  1. pulls lyrics from LRCLIB (free, no key),
  2. runs the AI blank-selector on the lines,
  3. keeps a sparse set of non-repeating, high-value blanks.

Both the web app (server.py) and the Telegram bot (bot.py) build on this, so
the pedagogy lives in exactly one place.

Lyrics from LRCLIB are crowdsourced and NOT licensed — fine for a private
prototype, but swap to a licensed source before any public/commercial launch.
"""

import json
import re
import time

import httpx

from ai_blank_selector import select_blanks

LRCLIB_SEARCH = "https://lrclib.net/api/search"
USER_AGENT = "Chorus/0.1 (English-learning prototype)"
MAX_LINES = 40      # lyric lines sent to the model
MAX_BLANKS = 8      # blanks kept for the exercise (kept sparse on purpose)


class ExerciseError(Exception):
    """Something went wrong building an exercise; the message is user-facing."""


def norm(s):
    return " ".join((s or "").lower().split())


def parse_lrc(lrc):
    """Turn an LRC string into time-ordered {time, text} lines."""
    out = []
    for raw in lrc.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        text = re.sub(r"\[[^\]]*\]", "", raw).strip()
        if not stamps or not text:
            continue
        for mm, ss in stamps:
            out.append({"time": round(int(mm) * 60 + float(ss), 2), "text": text})
    out.sort(key=lambda x: x["time"])
    return out


def _fetch_lyrics(artist, title):
    # Retry — this network throws transient SSL EOFs.
    results, last_err = None, None
    for attempt in range(4):
        try:
            r = httpx.get(LRCLIB_SEARCH,
                          params={"artist_name": artist, "track_name": title},
                          headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            results = r.json()
            break
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    if results is None:
        raise ExerciseError("Couldn't reach the lyrics service — try again in a moment.")
    return results


def choose_blanks(line_texts, level="intermediate"):
    """Return {line_index: {answer, category, why}} — sparse and non-repeating.

    Works on any lyric lines (from LRCLIB or supplied by the user).
    """
    if not line_texts:
        raise ExerciseError("No lyric lines to work with.")
    try:
        data = json.loads(select_blanks(line_texts, level=level))
    except Exception as e:
        raise ExerciseError("Couldn't choose blanks: {}".format(e))

    blanks_by_text = {}
    for ln in data.get("lines", []):
        if ln.get("blanks"):
            blanks_by_text.setdefault(norm(ln.get("original", "")), ln["blanks"])

    used, chosen = set(), {}
    for i, text in enumerate(line_texts):
        if len(chosen) >= MAX_BLANKS:
            break
        for b in blanks_by_text.get(norm(text), []):
            answer = (b.get("answer") or "").strip()
            key = norm(answer)
            if not key or key in used:
                continue
            used.add(key)
            chosen[i] = {"answer": answer,
                         "category": b.get("category", ""),
                         "why": b.get("why", "")}
            break
    if not chosen:
        raise ExerciseError("Couldn't find anything worth blanking here.")
    return chosen


def fetch_lyric_lines(artist, title):
    """Fetch plain lyric lines from LRCLIB. Returns (lines, {artist, title})."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        raise ExerciseError("I need at least a song title to look up the lyrics.")

    results = _fetch_lyrics(artist, title)
    best = next((x for x in results if x.get("plainLyrics") or x.get("syncedLyrics")), None)
    if not best:
        who = "{} - {}".format(artist, title) if artist else title
        raise ExerciseError("No lyrics found for '{}'. Check the spelling?".format(who))

    if best.get("plainLyrics"):
        lines = [ln.strip() for ln in best["plainLyrics"].splitlines() if ln.strip()]
    else:
        lines = [t["text"] for t in parse_lrc(best["syncedLyrics"])]
    lines = lines[:MAX_LINES]
    if not lines:
        raise ExerciseError("Found the song but couldn't read the lyrics.")
    return lines, {"artist": best.get("artistName"), "title": best.get("trackName")}


def make_exercise(artist, title, level="intermediate"):
    """Return {matched, level, blank_count, lines} or raise ExerciseError.

    `lines` is every lyric line in order (for read-along); only a sparse few
    carry a `blank` dict — the rest have blank=None.
    """
    line_texts, matched = fetch_lyric_lines(artist, title)
    level = (level or "intermediate").strip()
    chosen = choose_blanks(line_texts, level)

    lines = []
    for i, text in enumerate(line_texts):
        b = chosen.get(i)
        lines.append({
            "text": text,
            "blank": b,
            "blanked": re.sub(re.escape(b["answer"]), "___", text, count=1) if b else None,
        })

    return {
        "matched": matched,
        "level": level,
        "blank_count": len(chosen),
        "lines": lines,
    }
