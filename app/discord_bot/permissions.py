from __future__ import annotations

import discord

from app.config import get_settings
from app.models import GuildSettings
from app.ticket_service import support_roles_list


def is_owner(user_id: int) -> bool:
    return user_id in get_settings().owner_ids


def staff_ok(member: discord.Member, gs: GuildSettings) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    if is_owner(member.id):
        return True
    roles = {r.id for r in member.roles}
    for rid in support_roles_list(gs):
        if rid in roles:
            return True
    return False
