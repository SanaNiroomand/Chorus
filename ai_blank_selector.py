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
You are a music writer giving a language learner brief, interesting context
about a song they have just practised.

Give 3 or 4 short facts. Good material: what the song is really about, the
story behind how it came to be written, its cultural impact or chart history,
notable recording details, or what the title means idiomatically.

Rules:
- Do NOT quote or reproduce song lyrics. Describe and explain in your own words.
- Be accurate. If you are not confident you know this specific song, set
  "known" to false and return no facts rather than inventing any.
- One or two sentences per fact, plain language, in English.
- Skip anything a listener could work out just from the title.

Return ONLY a JSON object:
{"known": true, "facts": ["...", "...", "..."]}
"""


def song_facts(artist, title, model=MODEL):
    """Return the raw JSON string of background facts about a song."""
    client = OpenAI()
    who = "{} - {}".format(artist, title) if artist else title
    request = dict(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": FACTS_PROMPT},
            {"role": "user", "content": "Song: {}".format(who)},
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
