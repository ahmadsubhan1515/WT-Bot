from __future__ import annotations

import logging
import re

import discord
from discord import ui

from app.config import get_settings
from app.database import get_session_factory
from app.discord_bot.permissions import staff_ok
from app.ticket_service import (
    close_ticket_channel,
    count_open_for_user,
    get_or_create_guild_settings,
    get_ticket_by_channel,
    mark_claimed,
    next_ticket_number,
    register_ticket,
    support_roles_list,
)

log = logging.getLogger("warrior.views")


def _safe_slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-_]+", "-", name.lower()).strip("-")
    return (s or "user")[:32]


class OpenTicketModal(ui.Modal, title="Warrior Ticket"):
    subject = ui.TextInput(label="Subject", max_length=120, placeholder="Short summary", required=True)
    description = ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=False,
        placeholder="Describe your issue…",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != get_settings().guild_id:
            await interaction.response.send_message("This bot is locked to one server.", ephemeral=True)
            return
        assert isinstance(interaction.user, discord.Member)

        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            if not gs.ticket_category_id:
                await interaction.response.send_message(
                    "Ticket category is not set yet. Ask an admin to configure it in the **Warrior Dashboard**.",
                    ephemeral=True,
                )
                return

            n_open = await count_open_for_user(session, interaction.guild.id, interaction.user.id)
            if n_open >= gs.max_open_per_user:
                await interaction.response.send_message(
                    f"You already have **{n_open}** open ticket(s) (max {gs.max_open_per_user}).",
                    ephemeral=True,
                )
                return

            cat = interaction.guild.get_channel(int(gs.ticket_category_id))
            if not isinstance(cat, discord.CategoryChannel):
                await interaction.response.send_message("Ticket category is invalid. Fix it in the dashboard.", ephemeral=True)
                return

            num = await next_ticket_number(session, interaction.guild.id)
            slug = _safe_slug(interaction.user.display_name)
            channel_name = f"ticket-{num:04d}-{slug}"

            overwrites: dict = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    manage_channels=True,
                ),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                ),
            }
            for rid in support_roles_list(gs):
                role = interaction.guild.get_role(int(rid))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                        manage_messages=True,
                    )

            await interaction.response.defer(ephemeral=True, thinking=True)

            try:
                ch = await interaction.guild.create_text_channel(
                    name=channel_name[:100],
                    category=cat,
                    overwrites=overwrites,
                    reason=f"Ticket by {interaction.user}",
                )
            except discord.HTTPException as e:
                await interaction.followup.send(f"Could not create channel: `{e}`", ephemeral=True)
                return

            subj = str(self.subject.value).strip()
            desc = str(self.description.value or "").strip()
            await register_ticket(session, interaction.guild.id, ch.id, interaction.user.id, subj)

        emb = discord.Embed(
            title=subj,
            description=desc or "*No extra details provided.*",
            color=discord.Color.dark_red(),
        )
        emb.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        emb.set_footer(text="Use the buttons below • Staff will assist you soon")

        await ch.send(
            content=f"{interaction.user.mention} — ticket opened.",
            embed=emb,
            view=TicketManageView(),
        )
        await interaction.followup.send(f"Ticket created: {ch.mention}", ephemeral=True)


class TicketPanelView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(label="Create ticket", style=discord.ButtonStyle.danger, custom_id="wt:panel:create", emoji="⚔️")
    async def create_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.guild is None or interaction.guild.id != get_settings().guild_id:
            await interaction.response.send_message("Wrong server.", ephemeral=True)
            return
        await interaction.response.send_modal(OpenTicketModal())


class TicketManageView(ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(label="Close", style=discord.ButtonStyle.secondary, custom_id="wt:ticket:close", row=0)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        if interaction.guild.id != get_settings().guild_id:
            await interaction.response.send_message("Wrong server.", ephemeral=True)
            return

        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Not an active ticket.", ephemeral=True)
                return
            assert isinstance(interaction.user, discord.Member)
            allowed = interaction.user.id == t.opener_id or staff_ok(interaction.user, gs)
            if not allowed:
                await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
                return

        await interaction.response.defer(ephemeral=True)
        async with get_session_factory()() as session:
            ok, msg = await close_ticket_channel(
                interaction.client,
                session,
                interaction.channel,
                closed_by=str(interaction.user),
                guild_id=interaction.guild.id,
            )
        await interaction.followup.send(msg, ephemeral=True)

    @ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="wt:ticket:claim", row=0)
    async def claim_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        assert isinstance(interaction.user, discord.Member)

        async with get_session_factory()() as session:
            gs = await get_or_create_guild_settings(session, interaction.guild.id)
            t = await get_ticket_by_channel(session, interaction.channel.id)
            if not t or t.status != "open":
                await interaction.response.send_message("Not an active ticket.", ephemeral=True)
                return
            if not staff_ok(interaction.user, gs):
                await interaction.response.send_message("Only support staff can claim.", ephemeral=True)
                return
            if t.claimed_by_id and t.claimed_by_id != interaction.user.id:
                await interaction.response.send_message("Already claimed by someone else.", ephemeral=True)
                return
            await mark_claimed(session, interaction.channel.id, interaction.user.id)

        await interaction.response.send_message(f"Claimed by {interaction.user.mention}.", ephemeral=False)
