# Chorus

A Telegram bot that turns your own music into language exercises.

Send it a song. It finds the lyrics, blanks out the words actually worth
learning — idioms, phrasal verbs, slang, grammar patterns — and sends the lyric
back as a fill-in-the-blank worksheet. Listen, reply with your answers, and it
marks them and explains each one.

## Any language

Not just English. Send a song in Spanish, French, German, Portuguese, Italian,
Persian — whatever you like — and it works out which language it is on its own.
There is no setting to change.

What it picks adapts to the language it finds. A Spanish song gets Spanish
grammar patterns and fixed expressions; an English one gets phrasal verbs and
English idioms. The explanations come back in English, so you can study a
language you barely know yet and still follow why each word matters.

Languages written without spaces between words (Chinese, Japanese, Thai) are
rougher, since the blanks and the answer checking both work word by word.

## Using it

Message the bot and send a music file. Everything else is on the menu.

| Button | What it does |
|---|---|
| 🎵 New song | Start over with another track |
| 🔁 Try again | Re-attempt the current song |
| 🎚 Level | Beginner, intermediate or advanced |
| 💡 About this song | Background, plus the references in the lyrics explained |
| ❓ How it works | Short reminder |

Beginners also get a shuffled word bank, so it's a matter of choosing rather
than recalling from nothing.

If the file has no title/artist tags, reply `Artist - Title` and it will look
the lyrics up, or just paste the lyrics yourself.

## Running it

Needs Python 3.11+ and three environment variables:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.5
TELEGRAM_TOKEN=...        # from @BotFather
```

Then:

```bash
pip install -r requirements.txt
python bot.py
```

Locally you can put those in a `.env` file instead (see `.env.example`); on a
host, set them as environment variables. `start.sh` is the entry point for
platforms that look for one.

Only run one copy at a time — Telegram allows a single poller per token.

## How it fits together

| File | Job |
|---|---|
| `bot.py` | The Telegram bot: menu, worksheets, grading |
| `exercise.py` | Fetches lyrics from LRCLIB, picks a sparse set of blanks |
| `ai_blank_selector.py` | The OpenAI calls — choosing blanks, and song background |

Blanks are chosen for teaching value rather than rarity or position: at most one
per line, never the same word twice, and only a handful per song. When the model
finds nothing worth teaching, a simple fallback picks something so you always
get an exercise.

## Note on lyrics

Lyrics come from [LRCLIB](https://lrclib.net), which is free and crowdsourced
but not licensed. That's fine for personal use; sort out licensing before
opening this up to other people.
