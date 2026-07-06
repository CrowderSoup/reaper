# Reaper

Self-hosted, multi-tenant Discord moderation bot + admin platform. Built first as a
spam/security defense tool (Module 1) for the AFSPECWAR Discord, architected so
future modules (PAST tracker, FAQ bot, etc.) can be added without touching the
multi-tenancy, auth, or deployment layers. See the project spec for full context.

## Stack

Python 3.12, discord.py 2.x, FastAPI + HTMX, SQLAlchemy 2.x async (asyncpg),
Alembic, PostgreSQL, APScheduler, Kamal.

## Local development

1. Install [uv](https://docs.astral.sh/uv/), then:
   ```
   uv sync
   ```
2. Copy `.env.example` to `.env` and fill in:
   - A Discord application's bot token + OAuth2 client id/secret
     (developer portal, redirect URI `http://localhost:8000/auth/callback`)
   - A local or DO Managed Postgres `DATABASE_URL` (asyncpg driver)
   - `SESSION_SECRET_KEY` (`openssl rand -hex 32`)
3. Run migrations:
   ```
   uv run --env-file .env alembic upgrade head
   ```
4. Run the bot and web processes (separate terminals):
   ```
   uv run --env-file .env python -m reaper.bot.main
   uv run --env-file .env uvicorn reaper.web.main:app --reload
   ```

## Migrations

```
uv run --env-file .env alembic revision --autogenerate -m "description"
uv run --env-file .env alembic upgrade head
```

## Deployment

Kamal, see `config/deploy.yml`. Two roles off one image: `web` (FastAPI, exposed)
and `bot` (discord.py gateway, no exposed port). Run migrations before promoting
new containers:

```
kamal app exec --roles=web -- uv run alembic upgrade head
kamal deploy
```

Secrets (`DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_SECRET`, `DATABASE_URL`,
`SESSION_SECRET_KEY`, ...) are supplied via Kamal secrets, never committed.
