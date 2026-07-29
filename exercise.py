"""
exercise.py — Chorus's shared core.

Given an artist + title it:
  1. pulls lyrics from LRCLIB (free, no key),
  2. runs the AI blank-selector on the lines,
  3. keeps a sparse set of non-repeating, high-value blanks.

bot.py builds on this, so the pedagogy lives in exactly one place.

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
MAX_LINES = 200     # sanity cap only; real songs are well under this, so the
                    # learner always gets the whole lyric, never a truncation
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


# Function words: never interesting as a blank, so the fallback skips them.
_STOPWORDS = {
    "that", "this", "with", "your", "from", "have", "they", "them", "then",
    "were", "will", "what", "when", "would", "could", "should", "there",
    "their", "been", "just", "like", "know", "want", "come", "into", "only",
    "over", "than", "some", "more", "make", "take", "here", "very", "much",
    "cant", "dont", "wont", "gonna", "wanna", "yeah", "ooh", "aah", "oooh",
}


def _fallback_blanks(line_texts, limit=MAX_BLANKS):
    """Pick something reasonable when the model declines to choose anything.

    Longest content word per line, no repeats, spread across the song. These
    are weaker than the model's picks — chosen for length rather than teaching
    value — which is why the caller warns the learner.
    """
    candidates = []
    for i, text in enumerate(line_texts):
        best = ""
        for w in re.findall(r"[^\W\d_]{4,}", text, re.UNICODE):
            if w.lower() in _STOPWORDS:
                continue
            if len(w) > len(best):
                best = w
        if best:
            candidates.append((len(best), i, best))

    candidates.sort(key=lambda c: -c[0])          # longest words first
    used, chosen = set(), {}
    for _, i, word in candidates:
        if len(chosen) >= limit:
            break
        if norm(word) in used:
            continue
        used.add(norm(word))
        chosen[i] = {"answer": word, "category": "vocab",
                     "why": "Picked automatically — worth checking you heard it right."}
    return dict(sorted(chosen.items()))


def choose_blanks(line_texts, level="intermediate"):
    """Return ({line_index: {answer, category, why}}, warning_or_None).

    Always returns blanks if the lyrics contain any usable words: when the
    model picks nothing, a simple heuristic fills in rather than refusing to
    build an exercise at all. The warning tells the caller to say so.

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
    if chosen:
        return chosen, None

    # The model found nothing it considered teachable. Rather than refuse to
    # build an exercise, fall back to a heuristic and say the picks are weaker.
    chosen = _fallback_blanks(line_texts)
    if not chosen:
        raise ExerciseError("These lyrics don't have enough words to work with.")
    return chosen, ("I couldn't find much genuinely worth teaching in this one, "
                    "so these blanks are simple word-recognition rather than "
                    "idioms or grammar.")


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
