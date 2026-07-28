"""
app.py — Chorus MVP: listen to a song on YouTube, then recall the blanks.

Flow: paste a YouTube link + level -> listen to the full song -> reveal the
recall exercise -> fill blanks from memory -> check against the AI answer key.

Run:  streamlit run app.py
"""

import json

import streamlit as st

from ai_blank_selector import select_blanks
from lyrics_fetch import extract_video_id, fetch_transcript_lines, LyricsError

st.set_page_config(page_title="Chorus", page_icon="🎵")

MAX_LINES = 12

CATEGORY_LABELS = {
    "idiom": "idiom",
    "slang": "slang",
    "grammar": "grammar pattern",
    "vocab": "vocabulary",
    "collocation": "collocation",
    "phrasal_verb": "phrasal verb",
}


def normalize(s: str) -> str:
    """Loose match: lowercase, collapse spaces, drop edge punctuation."""
    return " ".join((s or "").lower().split()).strip(".,!?;:\"'")


def watch_url(video_id: str) -> str:
    return "https://www.youtube.com/watch?v={}".format(video_id)


ss = st.session_state
ss.setdefault("phase", "start")   # start -> listen -> recall
ss.setdefault("video_id", None)
ss.setdefault("exercise", None)
ss.setdefault("checked", False)


def reset():
    ss.phase = "start"
    ss.video_id = None
    ss.exercise = None
    ss.checked = False


st.title("🎵 Chorus")
st.caption("Learn English through music — listen, then recall.")

# --- Phase 1: pick a song + level -------------------------------------------
if ss.phase == "start":
    url = st.text_input("YouTube link", placeholder="https://www.youtube.com/watch?v=...")
    level = st.radio("Your level", ["beginner", "intermediate", "advanced"],
                     index=1, horizontal=True)
    pasted = st.text_area(
        "Lyrics — one line per row",
        height=160,
        placeholder="Paste the lines you want to practice…",
        help="Leave blank to try fetching YouTube captions automatically "
             "(often blocked, so pasting is the reliable path).",
    )
    if st.button("Load song", type="primary"):
        try:
            vid = extract_video_id(url)
        except LyricsError as e:
            st.error(str(e)); st.stop()

        lines = [ln.strip() for ln in pasted.splitlines() if ln.strip()][:MAX_LINES]
        with st.spinner("Building your exercise…"):
            if not lines:  # nothing pasted — try captions as a convenience
                try:
                    lines = fetch_transcript_lines(vid, max_lines=MAX_LINES)
                except LyricsError as e:
                    st.error(str(e))
                    st.info("Paste the lyric lines in the box above and press "
                            "Load song again — YouTube is blocking auto-fetch here.")
                    st.stop()
            try:
                exercise = json.loads(select_blanks(lines, level=level))
            except Exception as e:
                st.error("Couldn't build the exercise: {}".format(e)); st.stop()
        ss.video_id, ss.exercise, ss.phase, ss.checked = vid, exercise, "listen", False
        st.rerun()

# --- Phase 2: listen to the whole song --------------------------------------
elif ss.phase == "listen":
    st.video(watch_url(ss.video_id))
    st.info("▶️ Listen to the whole song first. This is about recall from memory, "
            "not real-time typing.")
    c1, c2 = st.columns(2)
    if c1.button("I've listened — start recall", type="primary"):
        ss.phase = "recall"; st.rerun()
    if c2.button("Pick another song"):
        reset(); st.rerun()

# --- Phase 3: recall ---------------------------------------------------------
elif ss.phase == "recall":
    with st.expander("▶️ Replay the song"):
        st.video(watch_url(ss.video_id))

    st.subheader("Fill in what you remember")
    # Only show lines that actually have blanks — never a full unblanked lyric.
    lines = [ln for ln in ss.exercise.get("lines", []) if ln.get("blanks")]

    for li, line in enumerate(lines):
        blanks = line.get("blanks", [])
        display = line.get("blanked", "")
        for n in range(1, len(blanks) + 1):
            display = display.replace("___", "**[{}]**".format(n), 1)
        st.markdown("{}. {}".format(li + 1, display))
        cols = st.columns(len(blanks))
        for bi in range(len(blanks)):
            cols[bi].text_input("[{}]".format(bi + 1), key="ans_{}_{}".format(li, bi),
                                label_visibility="collapsed",
                                placeholder="blank {}".format(bi + 1))

    c1, c2 = st.columns(2)
    if c1.button("Check answers", type="primary"):
        ss.checked = True
    if c2.button("Start over"):
        reset(); st.rerun()

    if ss.checked:
        st.divider()
        correct = total = 0
        for li, line in enumerate(lines):
            for bi, blank in enumerate(line.get("blanks", [])):
                total += 1
                user = ss.get("ans_{}_{}".format(li, bi), "")
                answer = blank.get("answer", "")
                ok = normalize(user) == normalize(answer)
                correct += int(ok)
                cat = CATEGORY_LABELS.get(blank.get("category", ""), blank.get("category", ""))
                line_md = "{} **{}**  ·  _{}_  \n&nbsp;&nbsp;{}".format(
                    "✅" if ok else "❌", answer, cat, blank.get("why", ""))
                if not ok:
                    line_md += "  \n&nbsp;&nbsp;you wrote: `{}`".format(user or "—")
                st.markdown(line_md)
        st.success("Score: {} / {}".format(correct, total))
