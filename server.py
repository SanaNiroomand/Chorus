"""
server.py — Chorus web backend (Starlette).

Serves the single-page read-along UI and one JSON endpoint. The exercise logic
(LRCLIB lookup + AI blanks + dedup) lives in exercise.py, shared with the
Telegram bot (bot.py).

Run:  python -m uvicorn server:app --port 8000
"""

import os

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from exercise import make_exercise, ExerciseError

HERE = os.path.dirname(os.path.abspath(__file__))


def build_payload(body):
    ex = make_exercise(body.get("artist"), body.get("title"), body.get("level"))
    ex["video_id"] = (body.get("video_id") or "").strip()
    return ex


async def index(request):
    return FileResponse(os.path.join(HERE, "index.html"),
                        headers={"Cache-Control": "no-cache"})


async def exercise(request):
    body = await request.json()
    try:
        payload = await run_in_threadpool(build_payload, body)
    except ExerciseError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(payload)


app = Starlette(routes=[
    Route("/", index, methods=["GET"]),
    Route("/exercise", exercise, methods=["POST"]),
])
