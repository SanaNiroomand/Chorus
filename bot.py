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
import json
import os
import random
import re
import sys
import tempfile
import time

import av
import httpx
from dotenv import load_dotenv

import stats
from exercise import choose_blanks, fetch_lyric_lines, ExerciseError, MAX_LINES
from ai_blank_selector import song_facts

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = (os.getenv("ADMIN_CHAT_ID") or "").strip()
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


TG_LIMIT = 4096   # Telegram rejects longer messages outright


def _chunks(text, limit=TG_LIMIT):
    """Split at line boundaries so HTML tags are never cut in half."""
    if len(text) <= limit:
        yield text
        return
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:          # a single monstrous line still has to give
            if buf:
                yield buf
                buf = ""
            yield line[:limit]
            line = line[limit:]
        if buf and len(buf) + len(line) + 1 > limit:
            yield buf
            buf = line
        else:
            buf = "{}\n{}".format(buf, line) if buf else line
    if buf:
        yield buf


def send(chat, text):
    """Send text, splitting it if it exceeds Telegram's per-message limit.

    A full lyric sheet routinely runs past 4096 characters, and Telegram
    rejects the whole message rather than truncating it.
    """
    last = None
    for chunk in _chunks(text):
        last = tg("sendMessage", chat_id=chat, text=chunk, parse_mode="HTML",
                  disable_web_page_preview=True)
    return last


def send_progress(chat, st, text):
    """Send a 'working on it' message, remembered so it can be tidied away later.

    These are only useful while the user waits; once the worksheet arrives they
    are clutter, so clear_progress() deletes them.
    """
    resp = send(chat, text)
    try:
        st.setdefault("progress_msgs", []).append(resp["result"]["message_id"])
    except (KeyError, TypeError):
        pass          # could not read the id; nothing to clean up later
    return resp


def clear_progress(chat, st):
    """Delete the progress messages sent for the current song."""
    for mid in st.pop("progress_msgs", []):
        tg("deleteMessage", chat_id=chat, message_id=mid)


# ---------- menu ----------
#
# Everything is a reply keyboard: the grid that sits above the phone keyboard
# and stays there. Tapping a button sends its label as an ordinary message,
# which handle() intercepts. There are no inline buttons anywhere.

BTN_NEW = "🎵 New song"
BTN_RETRY = "🔁 Try again"
BTN_LEVEL = "🎚 Level"
BTN_FACTS = "💡 About this song"
BTN_HELP = "❓ How it works"
BTN_FEEDBACK = "💬 Feedback"

BTN_BEGINNER = "🌱 Beginner"
BTN_INTERMEDIATE = "🎯 Intermediate"
BTN_ADVANCED = "🔥 Advanced"
BTN_BACK = "◀️ Back"

LEVEL_LABEL = {"beginner": BTN_BEGINNER, "intermediate": BTN_INTERMEDIATE,
               "advanced": BTN_ADVANCED}
LEVEL_BY_BUTTON = {BTN_BEGINNER: "beginner", BTN_INTERMEDIATE: "intermediate",
                   BTN_ADVANCED: "advanced"}


def _keyboard(rows):
    # one_time_keyboard collapses it after a tap instead of sitting on screen
    # permanently; the user reopens it from the keyboard icon when wanted.
    return {"keyboard": [[{"text": t} for t in row] for row in rows],
            "resize_keyboard": True, "one_time_keyboard": True}


MAIN_MENU = _keyboard([
    [BTN_NEW, BTN_RETRY],
    [BTN_LEVEL, BTN_FACTS],
    [BTN_HELP, BTN_FEEDBACK],
])

# Shown while the user is writing feedback, so Back is the way out.
FEEDBACK_MENU = _keyboard([[BTN_BACK]])

# Shown only while choosing a level, then the main menu comes back.
LEVEL_MENU = _keyboard([
    [BTN_BEGINNER],
    [BTN_INTERMEDIATE],
    [BTN_ADVANCED],
    [BTN_BACK],
])


def send_menu(chat, text, menu=None):
    """Send a message and (re)attach a keyboard, the main menu by default."""
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
              disable_web_page_preview=True, reply_markup=menu or MAIN_MENU)


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
    st.pop("sheet", None)
    st.pop("lines", None)
    st.pop("facts", None)
    st.pop("concepts", None)
    st.pop("progress_msgs", None)   # ids from a previous song are stale now
    st["phase"] = None


# ---------- worksheet + grading ----------

def build_worksheet(chat, st, line_texts, matched):
    line_texts = line_texts[:MAX_LINES]
    # The lyric lookup above takes ~1s; this AI call is the real wait (~15s),
    # so say so rather than leaving the user staring at "finding the lyrics".
    send_progress(chat, st, "🧠 Got the lyrics. Now picking the words worth learning — "
                            "this is the slow part, about 15 seconds…")
    try:
        chosen, _ = choose_blanks(line_texts, st.get("level", "intermediate"))
    except ExerciseError as e:
        # Without this the progress message would sit there forever with no
        # explanation of why nothing arrived.
        clear_progress(chat, st)
        send(chat, "😕 {} Try another song.".format(esc(str(e))))
        return

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
    # Kept so "Try again" can re-present the same exercise without paying for
    # another AI call. The plain lines are kept too, so "About this song" can
    # spot references that actually occur in the lyrics.
    st["sheet"] = "\n".join(out_lines)
    st["lines"] = line_texts

    clear_progress(chat, st)   # the waiting is over; tidy the chatter away
    stats.record("built", chat,
                 level=st.get("level"),
                 blanks=len(blanks),
                 song=("{} — {}".format(clean_artist(matched.get("artist")),
                                        matched.get("title")) if matched else None))
    present_worksheet(chat, st)


def word_bank(blanks):
    """Shuffled answers, shown to beginners so they choose rather than produce.

    Recognition is easier than recall, which is the point at beginner level;
    higher levels get no bank so the recall stays genuine.
    """
    words = [b["answer"] for b in blanks]
    random.shuffle(words)
    return " · ".join("<code>{}</code>".format(esc(w)) for w in words)


def present_worksheet(chat, st):
    """Send the exercise: header, the gapped lyric, and a bank for beginners."""
    blanks, matched = st["blanks"], st.get("matched")
    head = ""
    if matched:
        head = "🎧 <b>{} — {}</b>\n".format(
            esc(clean_artist(matched["artist"])), esc(matched["title"]))
    n = len(blanks)
    shown = min(n, 3)                       # keep the example the size of the task
    numbers = " ".join(keycap(i) for i in range(1, shown + 1))
    examples = ["first missing word", "second missing word", "third missing word"][:shown]
    example_block = "\n".join("{}. {}".format(i + 1, w) for i, w in enumerate(examples))
    send(chat, head + (
        "There {} <b>{} gap{}</b> in the lyrics below, marked {}{}\n\n"
        "▶️ Play your song and listen for the missing words.\n"
        "✍️ Then send {} back in <b>one message</b>, numbered:\n\n"
        "<code>{}</code>\n\n"
        "Numbering keeps everything lined up. You can also just write one word "
        "per line in order, without numbers.\n"
        "Don't know one? Write <code>-</code> and I'll show you the answer."
    ).format("is" if n == 1 else "are", n, "" if n == 1 else "s",
             numbers, "" if n <= 3 else " and so on",
             "it" if n == 1 else "them all", example_block))
    send(chat, st["sheet"])
    # The menu rides along with the last message of the worksheet, so the
    # buttons stay reachable while the exercise is being answered rather than
    # only turning up once it is over.
    if st.get("level") == "beginner":
        send_menu(chat, "🌱 <b>Word bank</b> — every word you need is here, just shuffled. "
                        "Work out which one fits which gap:\n\n{}".format(word_bank(blanks)))
    else:
        send_menu(chat, "The menu is below whenever you need it.")


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
    send(chat, "🏁 <b>{} / {}</b> ({}%)  {}\n\n{}".format(
        correct, total, pct, tag, "\n".join(rows)))

    matched = st.get("matched")
    stats.record("graded", chat,
                 level=st.get("level"), correct=correct, total=total,
                 song=("{} — {}".format(clean_artist(matched.get("artist")),
                                        matched.get("title")) if matched else None))

    # Keep the exercise around so "Try again" costs nothing; only "New song"
    # throws it away.
    st["phase"] = "done"
    send_menu(chat, "Use the menu below: try this song again, hear about it, "
                    "or send another.")


# ---------- input handling ----------

def from_audio(chat, st, fid, size):
    if size and size > 19 * 1024 * 1024:
        send(chat, "That file is over 20 MB — Telegram won't let bots download files that big. "
                   "Try a shorter or lower-bitrate one.")
        return
    send_progress(chat, st, "🎵 Got it — finding the lyrics…")
    tmp = os.path.join(tempfile.gettempdir(), "chorus_{}".format(chat))
    title = artist = None
    if download(fid, tmp):
        title, artist = read_tags(tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not title:
        clear_progress(chat, st)
        send(chat, "I couldn't read the song from the file. Reply <b>Artist - Title</b> and "
                   "I'll fetch the lyrics, or paste the lyrics.")
        return
    try:
        lines, matched = fetch_lyric_lines(artist, title)
    except ExerciseError:
        clear_progress(chat, st)
        who = "{} — {}".format(artist, title) if artist else title
        send(chat, "Found the song (<b>{}</b>) but not its lyrics. Reply <b>Artist - Title</b> "
                   "of the exact release, or paste the lyrics.".format(esc(who)))
        return
    build_worksheet(chat, st, lines, matched)


def from_text_collect(chat, st, text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1 and " - " in lines[0]:
        artist, title = [p.strip() for p in lines[0].split(" - ", 1)]
        send_progress(chat, st, "🔎 Fetching lyrics for <b>{} — {}</b>…".format(
            esc(artist), esc(title)))
        try:
            lyr, matched = fetch_lyric_lines(artist, title)
        except ExerciseError as e:
            clear_progress(chat, st)
            send(chat, "😕 {} You can also paste the lyrics directly.".format(esc(str(e))))
            return
        build_worksheet(chat, st, lyr, matched)
    elif len(lines) >= 2:
        build_worksheet(chat, st, lines, None)
    else:
        send(chat, "Send a <b>music file</b> to start (or reply <b>Artist - Title</b>, "
                   "or paste the lyrics).")


def show_facts(chat, st):
    """Background on the song. Cached, so re-tapping costs nothing."""
    matched = st.get("matched")
    if not matched:
        send(chat, "I don't know which release this was, so I can't look it up.")
        return

    if "facts" not in st:
        send_progress(chat, st, "💡 Looking up the story behind this one…")
        try:
            data = json.loads(song_facts(matched.get("artist"), matched.get("title"),
                                         lines=st.get("lines")))
        except Exception:
            clear_progress(chat, st)
            send(chat, "😕 Couldn't fetch anything about this song right now.")
            return
        clear_progress(chat, st)
        if not data.get("known") or not (data.get("facts") or data.get("concepts")):
            send(chat, "🤷 I don't know enough about this song to say anything reliable.")
            return
        st["facts"] = data.get("facts") or []
        st["concepts"] = data.get("concepts") or []

    parts = []
    if st["facts"]:
        parts.append("\n\n".join("• {}".format(esc(f)) for f in st["facts"]))
    if st.get("concepts"):
        parts.append("📚 <b>Things mentioned in this song</b>\n\n" + "\n\n".join(
            "<b>{}</b> — {}".format(esc(c.get("term", "")), esc(c.get("explanation", "")))
            for c in st["concepts"]))

    send(chat, "💡 <b>About {} — {}</b>\n\n{}".format(
        esc(clean_artist(matched.get("artist"))), esc(matched.get("title")),
        "\n\n".join(parts)))


def deliver_feedback(chat, msg, text):
    """Pass a user's feedback to the admin, and keep a copy in the log."""
    who = msg.get("from") or {}
    name = " ".join(p for p in [who.get("first_name"), who.get("last_name")] if p)
    handle = "@{}".format(who["username"]) if who.get("username") else "no username"

    stats.record("feedback", chat, text=text[:2000])

    if ADMIN_CHAT_ID:
        send(int(ADMIN_CHAT_ID),
             "💬 <b>Feedback</b>\nfrom {} ({}, id <code>{}</code>)\n\n{}".format(
                 esc(name or "someone"), esc(handle), chat, esc(text[:3000])))


def set_level(chat, st, level):
    st["level"] = level
    extra = "  You'll get a word bank to choose from." if level == "beginner" else ""
    send_menu(chat, "Level set to <b>{}</b>.{}".format(LEVEL_LABEL[level], extra))


def retry(chat, st):
    if st.get("sheet") and st.get("blanks"):
        st["phase"] = "await"
        present_worksheet(chat, st)
    else:
        send(chat, "There's no exercise to retry. Send a music file to start one.")


def handle(upd):

    msg = upd.get("message") or upd.get("edited_message")
    if not msg:
        return
    chat = msg["chat"]["id"]
    st = STATE.setdefault(chat, {"level": "intermediate", "phase": None})
    text = (msg.get("text") or "").strip()
    low = text.lower()

    # /start is the one command kept: Telegram sends it automatically the first
    # time someone opens the bot, so it cannot be replaced by a button.
    if low.startswith("/start"):
        send_menu(chat, "🎵 <b>Chorus</b> — learn languages from your own songs.\n\n"
                        "<b>Send me a music file</b> and I'll find the lyrics, blank out the "
                        "words worth learning, and send it back as a worksheet.\n\n"
                        "Current level: <b>{}</b>\n\n"
                        "Everything else is on the buttons below.".format(
                            LEVEL_LABEL.get(st.get("level"), "")))
        return
    if low.startswith("/stats"):
        # Admin only and unadvertised, so users never learn it exists.
        if ADMIN_CHAT_ID and str(chat) == ADMIN_CHAT_ID:
            send(chat, stats.summary())
        return

    # While writing feedback, anything typed is the feedback itself - checked
    # before the menu below so a message that happens to match a button label
    # still reaches us. Back is the way out.
    if st.get("phase") == "feedback":
        if text == BTN_BACK:
            st["phase"] = st.pop("prev_phase", None)
            send_menu(chat, "No problem — nothing sent.")
        elif text:
            deliver_feedback(chat, msg, text)
            st["phase"] = st.pop("prev_phase", None)
            send_menu(chat, "🙏 Thank you — that's been passed on.")
        else:
            send_menu(chat, "Type your message and I'll pass it on.", FEEDBACK_MENU)
        return

    # Menu taps arrive as ordinary text, so they must be caught before the
    # answer grading below - otherwise tapping one mid-exercise would be
    # marked as a wrong answer.
    if text in LEVEL_BY_BUTTON:
        set_level(chat, st, LEVEL_BY_BUTTON[text])
        return
    if text == BTN_BACK:
        send_menu(chat, "Back to the menu.")
        return
    if text == BTN_NEW:
        reset(st)
        send(chat, "🎵 Send me a music file and I'll build the next one.")
        return
    if text == BTN_RETRY:
        retry(chat, st)
        return
    if text == BTN_LEVEL:
        send_menu(chat, "Pick your level:", LEVEL_MENU)
        return
    if text == BTN_FACTS:
        if st.get("matched"):
            show_facts(chat, st)
        else:
            send(chat, "Finish a song first and I'll tell you about it.")
        return
    if text == BTN_HELP:
        send(chat, WELCOME)
        return
    if text == BTN_FEEDBACK:
        # Remember what they were doing, so an exercise in progress survives.
        st["prev_phase"] = st.get("phase")
        st["phase"] = "feedback"
        send_menu(chat, "💬 What's on your mind? Anything at all — a bug, a song "
                        "that didn't work, something you'd like it to do.\n\n"
                        "Type it below and I'll pass it on.", FEEDBACK_MENU)
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
            send(chat, "Send me the missing words in one message, numbered:\n\n"
                       "<code>1. first missing word\n2. second missing word</code>")
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

    # No commands are advertised: everything is on the buttons. /start still
    # works because Telegram sends it on first contact, and /stats is admin
    # only and deliberately unlisted.
    tg("setMyCommands", commands=[])

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
