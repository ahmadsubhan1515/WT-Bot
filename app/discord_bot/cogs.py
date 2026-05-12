from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from app.config import get_settings
from app.database import get_session_factory
from app.discord_bot.bot import WarriorBot
from app.discord_bot.permissions import staff_ok
from app.discord_bot.views import TicketPanelView
from app.ticket_service import (
    close_ticket_channel,
    get_or_create_guild_settings,
    get_ticket_by_channel,
    update_guild_settings,
)

_GUILD = discord.Object(id=get_settings().guild_id)


class WarriorTicketsCog(commands.Cog):
    warrior = app_commands.Group(name="warrior", description="⚔️ Warrior Tickets — server tools")

    def __init__(self, bot: WarriorBot) -> None:
        self.bot = bot

    @warrior.command(name="panel", description="Post the ticket panel (Manage Server)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guilds(_GUILD)
    @app_commands.describe(channel="Channel for the panel (defaults to here)")
    async def warrior_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Use a text channel.", ephemeral=True)
            return

        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            title, desc = gs.panel_title, gs.panel_description

        emb = discord.Embed(title=title, description=desc, color=discord.Color.dark_red())
        emb.set_footer(text="Warrior Tickets • One server • Secure support")

        await interaction.response.defer(ephemeral=True)
        msg = await target.send(embed=emb, view=TicketPanelView())
        async with get_session_factory()() as session:
            await update_guild_settings(
                session,
                interaction.guild.id,
                panel_channel_id=target.id,
                panel_message_id=msg.id,
            )
        await interaction.followup.send(f"Panel posted in {target.mention}.", ephemeral=True)

    @warrior.command(name="close", description="Close this ticket (same as the Close button)")
    @app_commands.guilds(_GUILD)
    async def warrior_close(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Not an open ticket channel.", ephemeral=True)
                return
            assert isinstance(interaction.user, discord.Member)
            if interaction.user.id != t.opener_id and not staff_ok(interaction.user, gs):
                await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)
        async with get_session_factory()() as session:
            ok, msg = await close_ticket_channel(
                self.bot,
                session,
                interaction.channel,
                closed_by=str(interaction.user),
                guild_id=interaction.guild.id,
            )
        await interaction.followup.send(msg, ephemeral=True)

    @warrior.command(name="add", description="Add a member to the current ticket")
    @app_commands.guilds(_GUILD)
    @app_commands.describe(member="Member to add")
    async def warrior_add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Use this inside an open ticket.", ephemeral=True)
                return
            assert isinstance(interaction.user, discord.Member)
            if not staff_ok(interaction.user, gs):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return

        await interaction.channel.set_permissions(
            member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        )
        await interaction.response.send_message(f"Added {member.mention} to this ticket.", ephemeral=False)

    @warrior.command(name="remove", description="Remove a member from the current ticket")
    @app_commands.guilds(_GUILD)
    @app_commands.describe(member="Member to remove")
    async def warrior_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Use this inside an open ticket.", ephemeral=True)
                return
            assert isinstance(interaction.user, discord.Member)
            if not staff_ok(interaction.user, gs):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"Removed {member.mention}.", ephemeral=False)

    @warrior.command(name="rename", description="Rename the current ticket channel")
    @app_commands.guilds(_GUILD)
    @app_commands.describe(name="New name (ticket- prefix recommended)")
    async def warrior_rename(self, interaction: discord.Interaction, name: str) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Use this inside an open ticket.", ephemeral=True)
                return
            assert isinstance(interaction.user, discord.Member)
            if not staff_ok(interaction.user, gs):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
        clean = "-".join(name.lower().split())[:90]
        await interaction.channel.edit(name=clean or "ticket")
        await interaction.response.send_message("Renamed.", ephemeral=True)


async def setup(bot: WarriorBot) -> None:
    await bot.add_cog(WarriorTicketsCog(bot))
