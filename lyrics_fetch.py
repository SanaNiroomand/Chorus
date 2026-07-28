"""
lyrics_fetch.py — pull a song's lyric lines from a YouTube video's caption track.

We never host or redistribute audio or full lyrics. Captions are fetched only to
generate the recall exercise internally; the UI shows blanks, never the full text.
"""

import re

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled


class LyricsError(Exception):
    """Raised when we can't get usable lyric lines for a video."""


def extract_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    raise LyricsError("Couldn't find a YouTube video ID in that link.")


def fetch_transcript_lines(video_id: str, max_lines: int = 12) -> list:
    """Return up to `max_lines` caption cues as lyric lines."""
    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=["en"])
    except NoTranscriptFound:
        try:
            transcript = api.list(video_id).find_generated_transcript(["en"]).fetch()
        except Exception:
            raise LyricsError("No English captions found for this video.")
    except TranscriptsDisabled:
        raise LyricsError("Captions are disabled for this video.")
    except Exception as e:
        raise LyricsError("Couldn't fetch captions: {}".format(e))

    lines = []
    for snippet in transcript:
        text = snippet.text.strip().replace("\n", " ")
        # Skip empty cues and non-lyric markers like [Music] / [Applause].
        if not text or (text.startswith("[") and text.endswith("]")):
            continue
        lines.append(text)
        if len(lines) >= max_lines:
            break

    if not lines:
        raise LyricsError("Captions were found but contained no usable lyric lines.")
    return lines
