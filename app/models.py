from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.settings import settings


class Tenant(Base):
    """
    Тенант = клиентский бот.

    Тут живут:
    - токен / username бота
    - настройки подписки / депозита
    - ссылки (регистрация, депозит, мини-апп)
    - support + секрет для постбэков
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    owner_telegram_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )

    bot_token: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Канал для проверки подписки (опционально)
    gate_channel_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    gate_channel_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Партнёрские ссылки (могут быть шаблонами)
    ref_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    deposit_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Мини-апп (один общий для обычных пользователей)
    miniapp_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Поддержка
    support_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Секрет для постбэков (по желанию можно сделать обязательным)
    pb_secret: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Настройки гейта
    check_subscription: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    check_deposit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_deposit_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # связи
    users: Mapped[list["UserAccess"]] = relationship(
        "UserAccess",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant id={self.id} owner={self.owner_telegram_id} username={self.bot_username!r}>"


class UserAccess(Base):
    """
    Состояние доступа пользователя в рамках конкретного тенанта.

    Тут храним:
    - регистрацию
    - депозит
    - click_id / trader_id
    - username
    """

    __tablename__ = "user_accesses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_access_tenant_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Состояние гейта
    is_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_deposit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Идентификаторы для постбэков
    click_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        unique=True,
        nullable=True,
        index=True,
    )
    trader_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    total_deposits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserAccess tenant={self.tenant_id} user={self.user_id} reg={self.is_registered} dep={self.has_deposit}>"


class UserLang(Base):
    """
    Язык пользователя на уровне конкретного тенанта.
    """

    __tablename__ = "user_langs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_lang_tenant_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    lang: Mapped[str] = mapped_column(
        String(8),
        default=settings.lang_default,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserLang tenant={self.tenant_id} user={self.user_id} lang={self.lang}>"


class UserState(Base):
    """
    Техническое состояние диалога (последнее сообщение бота и т.п.).
    Нужно, чтобы редактировать одно сообщение вместо спама.
    """

    __tablename__ = "user_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "chat_id", name="uq_user_state_tenant_chat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    last_bot_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserState tenant={self.tenant_id} chat={self.chat_id} msg={self.last_bot_message_id}>"


class EventKind(str, enum.Enum):
    REG = "reg"
    FTD = "ftd"
    RD = "rd"


class Event(Base):
    """
    Лог событий от постбэков: регистрация / первый депозит / последующие депозиты.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_tenant_click", "tenant_id", "click_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    click_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trader_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # EventKind
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    raw_qs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event tenant={self.tenant_id} kind={self.kind} click={self.click_id}>"


class ContentOverride(Base):
    """
    Кастомизация экранов по тенанту / языку / экрану.
    Можно будет использовать позже, для MVP можно не трогать.
    """

    __tablename__ = "content_overrides"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "lang",
            "screen",
            name="uq_content_override_tenant_lang_screen",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    lang: Mapped[str] = mapped_column(String(8), nullable=False)
    screen: Mapped[str] = mapped_column(String(32), nullable=False)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_btn_text: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    buttons_json: Mapped[Optional[dict]] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ContentOverride tenant={self.tenant_id} lang={self.lang} screen={self.screen}>"