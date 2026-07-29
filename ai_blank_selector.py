"""
ai_blank_selector.py — Step 1 of Chorus.

Calls the OpenAI (ChatGPT) API to choose fill-in-the-blank targets from a few
lyric lines, with pedagogical reasoning. Deliberately tiny: no UI, no scoring,
no song fetching. It reads a handful of lines from a local file and prints the
raw JSON the model returns, so we can eyeball whether the choices are actually
smart before building anything on top of it.

Usage:
    python ai_blank_selector.py                 # reads lines.txt, level=intermediate
    python ai_blank_selector.py advanced        # override level
    python ai_blank_selector.py --lines other.txt

Lyrics are treated as private/internal, so lines.txt is gitignored.
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI, BadRequestError

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LEVELS = ("beginner", "intermediate", "advanced")

SYSTEM_PROMPT = """\
You are a pedagogy-driven language teacher building listen-then-recall exercises
from song lyrics. The lyrics may be in ANY language — detect which language they
are in, and choose items worth learning for a learner of THAT language. A learner
has just listened to the full song and will now try to recall specific words from
memory.

Be VERY selective — quality over coverage. MOST lines should have NO blank.
Only blank a word or short phrase when it is genuinely worth teaching: a real
idiom, a useful phrasal verb, distinctive slang, or a tricky grammar point.
Skip ordinary, literal, or easy lines entirely. A single well-chosen word is
better than a long phrase. Pick at most one per line, and across the whole song
choose only a small handful of the very best — at least one, but keep it sparse.

Never blank the same word or phrase more than once in the whole song. If a
teachable item recurs (like a repeated chorus phrase), blank only its FIRST
occurrence and leave every later occurrence filled in.

A good blank is one of:
  - an idiom or figurative expression
  - slang or colloquial usage
  - a grammar pattern worth internalizing (tense, preposition, article, etc.)
  - a genuinely useful, transferable vocabulary word or collocation
  - a phrasal verb (or a comparable notable construction in the song's language)

If nothing on a line is pedagogically worth blanking, return zero blanks for it.

Return ONLY a JSON object with this exact shape. IMPORTANT: include ONLY the
lines you actually chose to blank. Do NOT echo back lines you left alone -
omit them entirely. Keep every "why" to one short sentence.
{
  "level": "<the level you were given>",
  "lines": [
    {
      "original": "<the exact original line, copied verbatim>",
      "blanked":  "<the line with each chosen answer replaced by ___>",
      "blanks": [
        {
          "answer": "<exact text removed, matching the original line>",
          "category": "idiom | slang | grammar | vocab | collocation | phrasal_verb",
          "why": "<one short sentence IN ENGLISH: why this word/phrase is worth recalling>"
        }
      ]
    }
  ]
}

Calibrate difficulty to the level: beginner -> commoner, higher-frequency
targets; advanced -> subtler idioms, slang, and grammar. Keep each "answer"
exactly as it appears in the original line so it can be checked automatically.
"""


def select_blanks(lines, level="intermediate", model=MODEL):
    """Call the OpenAI API and return the raw JSON string the model produced."""
    client = OpenAI()  # reads OPENAI_API_KEY from the environment
    user_content = "Level: {}\n\nLines:\n{}".format(
        level, "\n".join("{}. {}".format(i + 1, ln) for i, ln in enumerate(lines))
    )
    request = dict(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    try:
        resp = client.chat.completions.create(temperature=0.2, **request)
    except BadRequestError as e:
        # Some newer/reasoning models only accept the default temperature.
        if "temperature" in str(e).lower():
            resp = client.chat.completions.create(**request)
        else:
            raise
    return resp.choices[0].message.content


FACTS_PROMPT = """\
You help a language learner understand a song they have just practised. You
give two kinds of help.

1. "facts" — 2 or 3 short points of background: what the song is really about,
   the story behind how it came to be written, its cultural impact, or notable
   recording details. Skip anything obvious from the title.

2. "concepts" — the things IN the song a non-native listener would not know:
   cultural or artistic references (an art movement, a film, a brand), places,
   historical figures, regional slang, or a phrase whose real meaning is not
   its literal one. This is the more valuable half. For example, a song that
   mentions "Art Deco" should explain what Art Deco is and what invoking it
   suggests about the person being described.

Rules:
- Do NOT quote or reproduce song lyrics anywhere in your answer. Name the term
  or reference on its own and explain it in your own words.
- Only list concepts that genuinely appear in the lyrics you are given.
- Explain what it is AND why it matters in this song, in 1-2 sentences.
- Skip ordinary vocabulary a dictionary would cover; this is for references and
  cultural knowledge, not word definitions.
- Be accurate. If you do not recognise the song, set "known" to false and
  return nothing rather than inventing anything.
- Plain language, in English. It is fine to return an empty concepts list if
  the song genuinely has no such references.

Return ONLY a JSON object:
{"known": true,
 "facts": ["...", "..."],
 "concepts": [{"term": "Art Deco", "explanation": "..."}]}
"""


def song_facts(artist, title, lines=None, model=MODEL):
    """Return the raw JSON string of background facts and concepts for a song.

    `lines` are the song's lyric lines. They are sent so the model can pick out
    references that actually occur in the song rather than guessing from the
    title; they are never echoed back, per the prompt.
    """
    client = OpenAI()
    who = "{} - {}".format(artist, title) if artist else title
    user_content = "Song: {}".format(who)
    if lines:
        user_content += "\n\nLyrics:\n{}".format("\n".join(lines))
    request = dict(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": FACTS_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    try:
        resp = client.chat.completions.create(temperature=0.4, **request)
    except BadRequestError as e:
        if "temperature" in str(e).lower():
            resp = client.chat.completions.create(**request)
        else:
            raise
    return resp.choices[0].message.content


def main():
    parser = argparse.ArgumentParser(description="Choose pedagogical blanks from lyric lines.")
    parser.add_argument("level", nargs="?", default="intermediate", choices=LEVELS)
    parser.add_argument("--lines", default="lines.txt", help="file with one lyric line per line")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")

    if not os.path.exists(args.lines):
        sys.exit("No lyric file at '{}'. Put 2-3 real lines in it, one per line.".format(args.lines))

    with open(args.lines, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        sys.exit("'{}' is empty — add a few lyric lines first.".format(args.lines))

    raw = select_blanks(lines, level=args.level)

    # Step 1 is about eyeballing the model's choices, so print the raw JSON as-is.
    print(raw)

    # Confirm it parses cleanly, since everything downstream will depend on that.
    try:
        json.loads(raw)
    except json.JSONDecodeError as e:
        print("\n[warn] response did not parse as JSON: {}".format(e), file=sys.stderr)


if __name__ == "__main__":
    main()
