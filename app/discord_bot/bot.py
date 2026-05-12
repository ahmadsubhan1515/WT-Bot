from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from app.config import get_settings
from app.database import get_session_factory
from app.state import set_bot
from app.ticket_service import (
    close_ticket_channel,
    get_or_create_guild_settings,
    get_ticket_by_channel,
    tickets_idle_for_auto_close,
    touch_activity,
)

from .permissions import is_owner, staff_ok
from .views import TicketManageView, TicketPanelView

log = logging.getLogger("warrior.discord")


def _is_owner(user_id: int) -> bool:
    return is_owner(user_id)


def _staff_ok(member: discord.Member, gs) -> bool:
    return staff_ok(member, gs)


class WarriorBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.guild_messages = True
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or("!wt_"), intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(TicketPanelView())
        self.add_view(TicketManageView())
        await self.load_extension("app.discord_bot.cogs")
        guild = discord.Object(id=get_settings().guild_id)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %s", get_settings().guild_id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "")
        if not self.auto_close_loop.is_running():
            self.auto_close_loop.start()

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.guild.id != get_settings().guild_id:
            return
        t = await get_ticket_by_channel_id(message.channel.id)
        if t:
            async with get_session_factory()() as session:
                await touch_activity(session, message.channel.id)

    async def close(self) -> None:
        if self.auto_close_loop.is_running():
            self.auto_close_loop.cancel()
        await super().close()

    @tasks.loop(minutes=10)
    async def auto_close_loop(self) -> None:
        gid = get_settings().guild_id
        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, gid)
            hours = int(gs.auto_close_hours or 0)
            if hours <= 0:
                return
            stale = await tickets_idle_for_auto_close(session, gid, hours)
        for t in stale:
            ch = self.get_channel(t.channel_id)
            if ch and isinstance(ch, discord.TextChannel):
                async with get_session_factory()() as session:
                    ok, msg = await close_ticket_channel(
                        self,
                        session,
                        ch,
                        closed_by="Auto-close (inactivity)",
                        guild_id=gid,
                    )
                    log.info("Auto-close ticket %s: %s %s", t.channel_id, ok, msg)


async def get_ticket_by_channel_id(channel_id: int):
    async with get_session_factory()() as session:
        return await get_ticket_by_channel(session, channel_id)


def build_bot() -> WarriorBot:
    return WarriorBot()


async def run_bot_forever(bot: WarriorBot) -> None:
    set_bot(bot)
    try:
        await bot.start(get_settings().discord_token)
    finally:
        set_bot(None)
