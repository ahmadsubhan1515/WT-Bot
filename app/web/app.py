from __future__ import annotations

import asyncio
import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import discord
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.discord_bot.bot import build_bot
from app.discord_bot.views import TicketPanelView
from app.models import Ticket
from app.state import set_bot
from app.ticket_service import (
    close_ticket_channel,
    dashboard_stats,
    get_or_create_guild_settings,
    list_tickets,
    save_full_guild_settings,
    support_roles_list,
    update_guild_settings,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _password_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(given.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


async def db_session() -> Any:
    async with get_session_factory()() as session:
        yield session


Db = Annotated[AsyncSession, Depends(db_session)]


def _session_ok(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_login(request: Request) -> None:
    if not _session_ok(request):
        raise HTTPException(status_code=401, detail="login_required")


class SettingsPayload(BaseModel):
    ticket_category_id: str | None = None
    transcript_channel_id: str | None = None
    support_role_ids: str = ""
    panel_title: str = "Warrior Support"
    panel_description: str = ""
    max_open_per_user: int = Field(default=3, ge=1, le=20)
    auto_close_hours: int = Field(default=0, ge=0, le=720)


class PanelBody(BaseModel):
    channel_id: str


def _parse_ids_csv(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").replace("\n", ",").split(","):
        p = part.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def _parse_opt_id(v: str | None) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if not s.isdigit():
        raise ValueError("Invalid snowflake")
    return int(s)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db()
        bot = build_bot()
        app.state.discord_bot = bot
        set_bot(bot)
        task = asyncio.create_task(bot.start(settings.discord_token))
        yield
        await bot.close()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        set_bot(None)

    app = FastAPI(title="Warrior Tickets", docs_url=None, redoc_url=None, lifespan=lifespan)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=60 * 60 * 24 * 14,
        same_site="lax",
        https_only=False,
    )

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Any:
        if _session_ok(request):
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    async def login_post(request: Request, password: str = Form(...)) -> Any:
        if _password_ok(password, settings.dashboard_password):
            request.session["auth"] = True
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Wrong password."},
            status_code=401,
        )

    @app.get("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, db: Db) -> Any:
        if not _session_ok(request):
            return RedirectResponse("/login", status_code=302)
        gs = await get_or_create_guild_settings(db, settings.guild_id)
        stats = await dashboard_stats(db, settings.guild_id)
        bot = getattr(request.app.state, "discord_bot", None)
        bot_ok = bool(bot and bot.is_ready())
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "guild_id": str(settings.guild_id),
                "gs": gs,
                "stats": stats,
                "bot_online": bot_ok,
                "support_roles_csv": ",".join(str(x) for x in support_roles_list(gs)),
            },
        )

    @app.get("/api/health")
    async def api_health(request: Request) -> Any:
        require_login(request)
        bot = getattr(request.app.state, "discord_bot", None)
        return {
            "bot_ready": bool(bot and bot.is_ready()),
            "guild_id": str(settings.guild_id),
        }

    @app.get("/api/stats")
    async def api_stats(request: Request, db: Db) -> Any:
        require_login(request)
        return await dashboard_stats(db, settings.guild_id)

    @app.get("/api/settings")
    async def api_settings_get(request: Request, db: Db) -> Any:
        require_login(request)
        gs = await get_or_create_guild_settings(db, settings.guild_id)
        return {
            "ticket_category_id": str(gs.ticket_category_id) if gs.ticket_category_id else "",
            "transcript_channel_id": str(gs.transcript_channel_id) if gs.transcript_channel_id else "",
            "support_role_ids": ",".join(str(x) for x in support_roles_list(gs)),
            "panel_title": gs.panel_title,
            "panel_description": gs.panel_description,
            "max_open_per_user": gs.max_open_per_user,
            "auto_close_hours": gs.auto_close_hours,
            "panel_channel_id": str(gs.panel_channel_id) if gs.panel_channel_id else "",
            "panel_message_id": str(gs.panel_message_id) if gs.panel_message_id else "",
        }

    @app.post("/api/settings")
    async def api_settings_post(request: Request, body: SettingsPayload, db: Db) -> Any:
        require_login(request)
        try:
            cat = _parse_opt_id(body.ticket_category_id)
            log_ch = _parse_opt_id(body.transcript_channel_id)
        except ValueError:
            raise HTTPException(400, "Invalid channel ID")
        roles = _parse_ids_csv(body.support_role_ids)
        await save_full_guild_settings(
            db,
            settings.guild_id,
            ticket_category_id=cat,
            transcript_channel_id=log_ch,
            support_role_ids=roles,
            panel_title=body.panel_title[:256],
            panel_description=body.panel_description,
            max_open_per_user=body.max_open_per_user,
            auto_close_hours=body.auto_close_hours,
        )
        return {"ok": True}

    @app.get("/api/tickets")
    async def api_tickets(request: Request, db: Db, status: str | None = None) -> Any:
        require_login(request)
        rows = await list_tickets(db, settings.guild_id, status=status if status else None)
        return [
            {
                "id": r.id,
                "channel_id": str(r.channel_id),
                "opener_id": str(r.opener_id),
                "subject": r.subject,
                "status": r.status,
                "claimed_by_id": str(r.claimed_by_id) if r.claimed_by_id else "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]

    @app.post("/api/tickets/{ticket_db_id}/close")
    async def api_ticket_close(request: Request, ticket_db_id: int, db: Db) -> Any:
        require_login(request)
        bot = getattr(request.app.state, "discord_bot", None)
        if bot is None or not bot.is_ready():
            raise HTTPException(503, "Discord bot is not connected")

        r = await db.execute(select(Ticket).where(Ticket.id == ticket_db_id, Ticket.guild_id == settings.guild_id))
        t = r.scalar_one_or_none()
        if not t or t.status != "open":
            raise HTTPException(404, "Ticket not found or already closed")

        ch = bot.get_channel(int(t.channel_id))
        if ch is None:
            from datetime import datetime as dt

            await db.execute(
                update(Ticket).where(Ticket.id == t.id).values(status="closed", closed_at=dt.utcnow())
            )
            await db.commit()
            raise HTTPException(410, "Channel missing; marked closed in database")

        if not isinstance(ch, discord.TextChannel):
            raise HTTPException(400, "Invalid channel type")

        ok, msg = await close_ticket_channel(bot, db, ch, closed_by="Dashboard", guild_id=settings.guild_id)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}

    @app.post("/api/panel/publish")
    async def api_panel_publish(request: Request, body: PanelBody, db: Db) -> Any:
        require_login(request)
        raw = body.channel_id.strip()
        if not raw.isdigit():
            raise HTTPException(400, "channel_id required")
        cid = int(raw)

        bot = getattr(request.app.state, "discord_bot", None)
        if bot is None or not bot.is_ready():
            raise HTTPException(503, "Discord bot is not connected")

        ch = bot.get_channel(cid)
        if not isinstance(ch, discord.TextChannel) or ch.guild.id != settings.guild_id:
            raise HTTPException(400, "Channel not in configured server or not text")

        gs = await get_or_create_guild_settings(db, settings.guild_id)
        emb = discord.Embed(
            title=gs.panel_title,
            description=gs.panel_description,
            color=discord.Color.dark_red(),
        )
        emb.set_footer(text="Warrior Tickets")

        msg = await ch.send(embed=emb, view=TicketPanelView())
        await update_guild_settings(
            db,
            settings.guild_id,
            panel_channel_id=ch.id,
            panel_message_id=msg.id,
        )
        return {"ok": True, "message_id": str(msg.id)}

    return app
