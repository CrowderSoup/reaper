"""Entry point for the web process: `uvicorn reaper.web.main:app`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from reaper.config import get_settings
from reaper.web import auth
from reaper.web.routes import dashboard, guild_settings, incidents

settings = get_settings()

app = FastAPI(title="Reaper Admin")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    max_age=settings.session_max_age_seconds,
)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(guild_settings.router)
app.include_router(incidents.router)


@app.get("/up", response_class=PlainTextResponse)
async def healthcheck() -> str:
    return "ok"
