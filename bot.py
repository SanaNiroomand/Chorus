"""
bot.py — Chorus Telegram bot (upload a song -> fill-in-the-blanks worksheet).

You send a music file. The bot reads its tags, finds the lyrics (LRCLIB), blanks
out the words worth learning, and sends the song back as a worksheet with
numbered gaps. You listen to your own file and reply with the answers (one per
line, or "1. word"); the bot grades them.

Fallbacks if the file has no tags: reply "Artist - Title" (bot fetches lyrics),
or paste the lyrics yourself.

Run:  python bot.py      (needs TELEGRAM_TOKEN in .env)
"""

import html
import os
import re
import sys
import tempfile
import time

import av
import httpx
from dotenv import load_dotenv

from exercise import choose_blanks, fetch_lyric_lines, ExerciseError, MAX_LINES

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
API = "https://api.telegram.org/bot{}".format(TOKEN)
FILE_API = "https://api.telegram.org/file/bot{}".format(TOKEN)

STATE = {}

WELCOME = (
    "🎵 <b>Chorus</b> — learn English from your songs.\n\n"
    "<b>Send me a music file.</b> I'll find the lyrics, blank out the words worth "
    "learning, and send it back as a worksheet with numbered gaps.\n\n"
    "Listen to your song and reply with the answers — one per line, or like "
    "<code>1. word</code>. I'll grade them.\n\n"
    "No tags on the file? Reply <b>Artist - Title</b> and I'll fetch the lyrics, "
    "or paste the lyrics yourself.\n\n"
    "• /level  beginner | intermediate | advanced\n"
    "• /stop — start over"
)


def esc(s):
    return html.escape("" if s is None else str(s), quote=False)


def clean_artist(a):
    seen = []
    for p in (a or "").split(","):
        p = p.strip()
        if p and p.lower() not in [s.lower() for s in seen]:
            seen.append(p)
    return ", ".join(seen)


_KEYCAPS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣",
            6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}


def keycap(n):
    # Telegram messages can't set text colour, so use the colourful keycap emoji.
    return _KEYCAPS.get(n, "[{}]".format(n))


def tg(method, **params):
    for attempt in range(3):
        try:
            return httpx.post("{}/{}".format(API, method), json=params, timeout=40).json()
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False}


def send(chat, text):
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              disable_web_page_preview=True)


def download(file_id, dest):
    j = tg("getFile", file_id=file_id)
    if not j.get("ok"):
        return None
    url = "{}/{}".format(FILE_API, j["result"]["file_path"])
    for attempt in range(3):
        try:
            r = httpx.get(url, timeout=120)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
            return dest
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def read_tags(path):
    """(title, artist) from the file's metadata, or (None, None)."""
    try:
        with av.open(path) as c:
            md = {(k or "").lower(): v for k, v in (c.metadata or {}).items()}
        return (md.get("title") or None), (md.get("artist") or None)
    except Exception:
        return None, None


def normalize(s):
    s = " ".join((s or "").lower().split())
    return re.sub(r"^[^\w']+|[^\w']+$", "", s)


def audio_of(msg):
    for key in ("audio", "voice"):
        if msg.get(key):
            return msg[key]["file_id"], msg[key].get("file_size") or 0
    d = msg.get("document")
    if d and d.get("mime_type", "").startswith("audio"):
        return d["file_id"], d.get("file_size") or 0
    return None, 0


def reset(st):
    st.pop("blanks", None)
    st.pop("matched", None)
    st["phase"] = None


# ---------- worksheet + grading ----------

def build_worksheet(chat, st, line_texts, matched):
    line_texts = line_texts[:MAX_LINES]
    # The lyric lookup above takes ~1s; this AI call is the real wait (~15s),
    # so say so rather than leaving the user staring at "finding the lyrics".
    send(chat, "🧠 Got the lyrics. Now picking the words worth learning — "
               "this is the slow part, about 15 seconds…")
    chosen = choose_blanks(line_texts, st.get("level", "intermediate"))

    blanks, out_lines, n = [], [], 0
    for i, text in enumerate(line_texts):
        c = chosen.get(i)
        if c:
            n += 1
            blanks.append({"n": n, "answer": c["answer"],
                           "why": c.get("why", ""), "category": c.get("category", "")})
            raw = re.sub(re.escape(c["answer"]), keycap(n), text, count=1)
            out_lines.append(esc(raw))
        else:
            out_lines.append(esc(text))

    st["blanks"], st["matched"], st["phase"] = blanks, matched, "await"

    head = ""
    if matched:
        head = "🎧 <b>{} — {}</b>\n".format(esc(clean_artist(matched["artist"])), esc(matched["title"]))
    send(chat, head + "Fill the <b>{} gap(s)</b>. Listen to your song, then reply with the "
                      "missing words — one per line (or like <code>1. word</code>).".format(len(blanks)))
    send(chat, "\n".join(out_lines))


def parse_answers(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    numbered = {}
    for ln in lines:
        m = re.match(r"^\s*(\d+)\s*[.)\-:]\s*(.+)$", ln)
        if m:
            numbered[int(m.group(1))] = m.group(2).strip()
    if numbered:
        return numbered
    if len(lines) == 1 and "," in lines[0]:
        return {i + 1: p.strip() for i, p in enumerate(lines[0].split(","))}
    return {i + 1: ln for i, ln in enumerate(lines)}


def grade_all(chat, st, text):
    blanks = st["blanks"]
    answers = parse_answers(text)
    correct, rows = 0, []
    for b in blanks:
        user = answers.get(b["n"], "")
        if normalize(user) == normalize(b["answer"]):
            correct += 1
            rows.append("✅ {} {} — {}".format(keycap(b["n"]), esc(b["answer"]), esc(b["why"])))
        else:
            rows.append("❌ {} {} — {}\n     <i>you wrote: {}</i>".format(
                keycap(b["n"]), esc(b["answer"]), esc(b["why"]), esc(user or "—")))
    total = len(blanks)
    pct = round(correct / total * 100) if total else 0
    tag = ("Perfect ear! 🎯" if pct == 100 else "Sharp listening!" if pct >= 70
           else "Nice start —" if pct >= 40 else "Keep at it —")
    send(chat, "🏁 <b>{} / {}</b> ({}%)  {}\n\n{}\n\nSend another song to go again.".format(
        correct, total, pct, tag, "\n".join(rows)))
    reset(st)


# ---------- input handling ----------

def from_audio(chat, st, fid, size):
    if size and size > 19 * 1024 * 1024:
        send(chat, "That file is over 20 MB — Telegram won't let bots download files that big. "
                   "Try a shorter or lower-bitrate one.")
        return
    send(chat, "🎵 Got it — finding the lyrics…")
    tmp = os.path.join(tempfile.gettempdir(), "chorus_{}".format(chat))
    title = artist = None
    if download(fid, tmp):
        title, artist = read_tags(tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not title:
        send(chat, "I couldn't read the song from the file. Reply <b>Artist - Title</b> and "
                   "I'll fetch the lyrics, or paste the lyrics.")
        return
    try:
        lines, matched = fetch_lyric_lines(artist, title)
    except ExerciseError:
        who = "{} — {}".format(artist, title) if artist else title
        send(chat, "Found the song (<b>{}</b>) but not its lyrics. Reply <b>Artist - Title</b> "
                   "of the exact release, or paste the lyrics.".format(esc(who)))
        return
    build_worksheet(chat, st, lines, matched)


def from_text_collect(chat, st, text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1 and " - " in lines[0]:
        artist, title = [p.strip() for p in lines[0].split(" - ", 1)]
        send(chat, "🔎 Fetching lyrics for <b>{} — {}</b>…".format(esc(artist), esc(title)))
        try:
            lyr, matched = fetch_lyric_lines(artist, title)
        except ExerciseError as e:
            send(chat, "😕 {} You can also paste the lyrics directly.".format(esc(str(e))))
            return
        build_worksheet(chat, st, lyr, matched)
    elif len(lines) >= 2:
        build_worksheet(chat, st, lines, None)
    else:
        send(chat, "Send a <b>music file</b> to start (or reply <b>Artist - Title</b>, "
                   "or paste the lyrics).")


def handle(upd):
    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return
    chat = msg["chat"]["id"]
    st = STATE.setdefault(chat, {"level": "intermediate", "phase": None})
    text = (msg.get("text") or "").strip()
    low = text.lower()

    if low.startswith("/start") or low.startswith("/help"):
        send(chat, WELCOME)
        return
    if low.startswith("/level"):
        parts = text.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg in ("beginner", "intermediate", "advanced"):
            st["level"] = arg
            send(chat, "Level set to <b>{}</b>.".format(arg))
        else:
            send(chat, "Usage: /level beginner | intermediate | advanced")
        return
    if low.startswith("/stop") or low.startswith("/new"):
        reset(st)
        send(chat, "Cleared. Send a <b>music file</b> to start.")
        return

    # a music file always starts a fresh exercise
    fid, size = audio_of(msg)
    if fid:
        reset(st)
        from_audio(chat, st, fid, size)
        return

    # waiting for the filled-in answers
    if st.get("phase") == "await":
        if text:
            grade_all(chat, st, text)
        else:
            send(chat, "Reply with your answers — one per line, or like <code>1. word</code>.")
        return

    if text:
        from_text_collect(chat, st, text)
        return

    send(chat, WELCOME)


def main():
    if not TOKEN:
        sys.exit("TELEGRAM_TOKEN not set. Add it to .env.")
    me = tg("getMe")
    if not me.get("ok"):
        sys.exit("Telegram rejected the token: {}".format(me))
    print("Chorus worksheet bot online as @{}".format(me["result"]["username"]), flush=True)

    offset = None
    while True:
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        try:
            data = httpx.get("{}/getUpdates".format(API), params=params, timeout=40).json()
        except Exception:
            time.sleep(2)
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            try:
                handle(upd)
            except Exception as e:
                print("handle error:", e, flush=True)


if __name__ == "__main__":
    main()
