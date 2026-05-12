from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class GuildSettings(Base):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tickets: Mapped[list["Ticket"]] = relationship(back_populates="guild_row")
    ticket_category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transcript_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    support_role_ids: Mapped[str] = mapped_column(Text, default="[]")
    panel_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    panel_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ticket_counter: Mapped[int] = mapped_column(Integer, default=0)
    panel_title: Mapped[str] = mapped_column(String(256), default="Warrior Support")
    panel_description: Mapped[str] = mapped_column(Text, default="Click the button below to open a private support ticket.")
    max_open_per_user: Mapped[int] = mapped_column(Integer, default=3)
    auto_close_hours: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("guild_settings.guild_id"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    opener_id: Mapped[int] = mapped_column(BigInteger, index=True)
    subject: Mapped[str] = mapped_column(String(256), default="Support")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    claimed_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    guild_row: Mapped[GuildSettings] = relationship(back_populates="tickets")
