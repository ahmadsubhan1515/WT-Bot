from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from typing import Any

import discord
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GuildSettings, Ticket


def _roles_from_json(raw: str) -> list[int]:
    try:
        data = json.loads(raw or "[]")
        return [int(x) for x in data if str(x).isdigit()]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _roles_to_json(ids: list[int]) -> str:
    return json.dumps(ids)


async def get_or_create_guild_settings(session: AsyncSession, guild_id: int) -> GuildSettings:
    r = await session.execute(select(GuildSettings).where(GuildSettings.guild_id == guild_id))
    row = r.scalar_one_or_none()
    if row:
        return row
    row = GuildSettings(guild_id=guild_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_guild_settings(
    session: AsyncSession,
    guild_id: int,
    *,
    ticket_category_id: int | None = None,
    transcript_channel_id: int | None = None,
    support_role_ids: list[int] | None = None,
    panel_channel_id: int | None = None,
    panel_message_id: int | None = None,
    panel_title: str | None = None,
    panel_description: str | None = None,
    max_open_per_user: int | None = None,
    auto_close_hours: int | None = None,
) -> GuildSettings:
    gs = await get_or_create_guild_settings(session, guild_id)
    if ticket_category_id is not None:
        gs.ticket_category_id = ticket_category_id
    if transcript_channel_id is not None:
        gs.transcript_channel_id = transcript_channel_id
    if support_role_ids is not None:
        gs.support_role_ids = _roles_to_json(support_role_ids)
    if panel_channel_id is not None:
        gs.panel_channel_id = panel_channel_id
    if panel_message_id is not None:
        gs.panel_message_id = panel_message_id
    if panel_title is not None:
        gs.panel_title = panel_title[:256]
    if panel_description is not None:
        gs.panel_description = panel_description
    if max_open_per_user is not None:
        gs.max_open_per_user = max(1, min(20, int(max_open_per_user)))
    if auto_close_hours is not None:
        gs.auto_close_hours = max(0, min(720, int(auto_close_hours)))
    await session.commit()
    await session.refresh(gs)
    return gs


async def save_full_guild_settings(
    session: AsyncSession,
    guild_id: int,
    *,
    ticket_category_id: int | None,
    transcript_channel_id: int | None,
    support_role_ids: list[int],
    panel_title: str,
    panel_description: str,
    max_open_per_user: int,
    auto_close_hours: int,
) -> GuildSettings:
    gs = await get_or_create_guild_settings(session, guild_id)
    gs.ticket_category_id = ticket_category_id
    gs.transcript_channel_id = transcript_channel_id
    gs.support_role_ids = _roles_to_json(support_role_ids)
    gs.panel_title = panel_title[:256]
    gs.panel_description = panel_description
    gs.max_open_per_user = max(1, min(20, int(max_open_per_user)))
    gs.auto_close_hours = max(0, min(720, int(auto_close_hours)))
    await session.commit()
    await session.refresh(gs)
    return gs


async def next_ticket_number(session: AsyncSession, guild_id: int) -> int:
    gs = await get_or_create_guild_settings(session, guild_id)
    gs.ticket_counter = int(gs.ticket_counter or 0) + 1
    n = gs.ticket_counter
    await session.commit()
    return n


async def count_open_for_user(session: AsyncSession, guild_id: int, user_id: int) -> int:
    q = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .where(
            Ticket.guild_id == guild_id,
            Ticket.opener_id == user_id,
            Ticket.status == "open",
        )
    )
    return int(q.scalar_one() or 0)


async def register_ticket(
    session: AsyncSession,
    guild_id: int,
    channel_id: int,
    opener_id: int,
    subject: str,
) -> Ticket:
    t = Ticket(
        guild_id=guild_id,
        channel_id=channel_id,
        opener_id=opener_id,
        subject=subject[:256],
        status="open",
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


async def get_ticket_by_channel(session: AsyncSession, channel_id: int) -> Ticket | None:
    r = await session.execute(select(Ticket).where(Ticket.channel_id == channel_id))
    return r.scalar_one_or_none()


async def list_tickets(session: AsyncSession, guild_id: int, status: str | None = None) -> list[Ticket]:
    q = select(Ticket).where(Ticket.guild_id == guild_id).order_by(Ticket.created_at.desc())
    if status:
        q = q.where(Ticket.status == status)
    r = await session.execute(q)
    return list(r.scalars().all())


async def dashboard_stats(session: AsyncSession, guild_id: int) -> dict[str, Any]:
    open_q = await session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.guild_id == guild_id, Ticket.status == "open")
    )
    closed_q = await session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.guild_id == guild_id, Ticket.status == "closed")
    )
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    closed_today = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .where(
            Ticket.guild_id == guild_id,
            Ticket.status == "closed",
            Ticket.closed_at.isnot(None),
            Ticket.closed_at >= start,
            Ticket.closed_at < end,
        )
    )
    return {
        "open": int(open_q.scalar_one() or 0),
        "closed_total": int(closed_q.scalar_one() or 0),
        "closed_today": int(closed_today.scalar_one() or 0),
    }


async def mark_claimed(session: AsyncSession, channel_id: int, staff_id: int) -> None:
    await session.execute(
        update(Ticket).where(Ticket.channel_id == channel_id, Ticket.status == "open").values(claimed_by_id=staff_id)
    )
    await session.commit()


async def touch_activity(session: AsyncSession, channel_id: int) -> None:
    await session.execute(
        update(Ticket)
        .where(Ticket.channel_id == channel_id, Ticket.status == "open")
        .values(last_activity_at=datetime.utcnow())
    )
    await session.commit()


async def tickets_idle_for_auto_close(session: AsyncSession, guild_id: int, hours: int) -> list[Ticket]:
    if hours <= 0:
        return []
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    r = await session.execute(
        select(Ticket).where(
            Ticket.guild_id == guild_id,
            Ticket.status == "open",
            Ticket.last_activity_at < cutoff,
        )
    )
    return list(r.scalars().all())


def format_transcript_lines(messages: list[discord.Message], channel_name: str) -> str:
    lines = [f"Transcript: #{channel_name}", "=" * 48, ""]
    for m in reversed(messages):
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{m.author} ({m.author.id})"
        content = (m.content or "").strip()
        if not content and m.attachments:
            content = "[attachments: " + ", ".join(a.url for a in m.attachments) + "]"
        if not content and m.embeds:
            content = "[embed]"
        lines.append(f"[{ts}] {author}: {content}")
    return "\n".join(lines)


async def close_ticket_channel(
    bot: Any,
    session: AsyncSession,
    channel: discord.abc.Messageable,
    *,
    closed_by: str,
    guild_id: int,
) -> tuple[bool, str]:
    if not hasattr(channel, "history"):
        return False, "Invalid channel"

    ch = channel  # type: ignore[assignment]
    t = await get_ticket_by_channel(session, ch.id)
    if not t or t.status != "open":
        return False, "Ticket not found or already closed"

    gs = await get_or_create_guild_settings(session, guild_id)
    msgs: list[discord.Message] = []
    async for m in ch.history(limit=500, oldest_first=False):
        msgs.append(m)
    msgs.reverse()
    body = format_transcript_lines(msgs, ch.name)
    file = discord.File(io.BytesIO(body.encode("utf-8")), filename=f"transcript-{ch.id}.txt")

    log_id = gs.transcript_channel_id
    if log_id:
        log_ch = bot.get_channel(int(log_id))
        if log_ch and isinstance(log_ch, discord.TextChannel):
            emb = discord.Embed(
                title="Ticket closed",
                description=f"Channel: `{ch.name}`\nClosed by: {closed_by}\nOpener: <@{t.opener_id}>",
                color=discord.Color.dark_red(),
            )
            emb.set_footer(text=f"Ticket DB id #{t.id}")
            await log_ch.send(embed=emb, file=file)
    else:
        await ch.send("No transcript channel configured; transcript not posted.", delete_after=15)

    await session.execute(
        update(Ticket)
        .where(Ticket.channel_id == ch.id)
        .values(status="closed", closed_at=datetime.utcnow(), claimed_by_id=t.claimed_by_id)
    )
    await session.commit()

    try:
        await ch.delete(reason=f"Ticket closed by {closed_by}")  # type: ignore[union-attr]
    except discord.HTTPException as e:
        return True, f"DB closed but channel delete failed: {e}"

    return True, "Closed"


def support_roles_list(gs: GuildSettings) -> list[int]:
    return _roles_from_json(gs.support_role_ids)
