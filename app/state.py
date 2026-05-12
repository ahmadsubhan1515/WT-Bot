from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.discord_bot.bot import WarriorBot

bot_instance: WarriorBot | None = None


def set_bot(bot: WarriorBot | None) -> None:
    global bot_instance
    bot_instance = bot
