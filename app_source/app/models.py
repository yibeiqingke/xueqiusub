from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DigestLog(Base):
    """记录每个用户每天是否已发送每日汇总，重启后仍可判定，避免漏发/重发。"""

    __tablename__ = "digest_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    digest_date: Mapped[date] = mapped_column(Date, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "digest_date", name="uq_digest_user_date"),)

    def __repr__(self) -> str:
        return f"<DigestLog user={self.user_id} date={self.digest_date}>"




class AiSummaryCache(Base):
    """缓存基于动态列表生成的 AI 总结，避免对相同订阅/动态重复调用大模型。"""

    __tablename__ = "ai_summary_caches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    summary_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self) -> str:
        return f"<AiSummaryCache key={self.cache_key}>"


class AppConfig(Base):
    """简单的键值配置表，用于后台可动态调整的运行期参数（如汇总小时/时区）。"""

    __tablename__ = "app_config"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))

    def __repr__(self) -> str:
        return f"<AppConfig {self.key}={self.value!r}>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, default=None)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # 每日汇总发送整点（0-23），每个用户自选；为空表示未设置，回退到全局默认 digest_hour
    digest_hour: Mapped[int | None] = mapped_column(Integer, default=None)

    # 每个用户自管的发信 SMTP 配置；为空时回退到全局 settings
    smtp_host: Mapped[str | None] = mapped_column(String(255), default=None)
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=None)
    smtp_username: Mapped[str | None] = mapped_column(String(320), default=None)
    smtp_password: Mapped[str | None] = mapped_column(String(320), default=None)
    smtp_from: Mapped[str | None] = mapped_column(String(320), default=None)
    smtp_ssl: Mapped[bool | None] = mapped_column(Boolean, default=None)
    smtp_starttls: Mapped[bool | None] = mapped_column(Boolean, default=None)

    # 每个用户自管的大模型 AI 总结配置；为空时回退到全局 settings / app_config
    llm_enabled: Mapped[bool | None] = mapped_column(Boolean, default=None)
    llm_api_base: Mapped[str | None] = mapped_column(String(255), default=None)
    llm_api_key: Mapped[str | None] = mapped_column(String(500), default=None)
    llm_model: Mapped[str | None] = mapped_column(String(120), default=None)

    # 群机器人 Webhook 推送配置 (飞书 / 钉钉 / 企业微信)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_type: Mapped[str | None] = mapped_column(String(32), default=None)
    webhook_url: Mapped[str | None] = mapped_column(String(500), default=None)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), default=None)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="user", cascade="all, delete-orphan")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class XueqiuAccount(Base):
    __tablename__ = "xueqiu_accounts"
    # 唯一约束需包含 platform：不同平台（雪球/微博等）允许同数字 ID 共存（旧库由启动迁移重建）
    __table_args__ = (UniqueConstraint("platform", "xueqiu_user_id", "feed_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), default="xueqiu")
    xueqiu_user_id: Mapped[str] = mapped_column(String(500), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    feed_type: Mapped[str] = mapped_column(String(32), default="all")
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # A) 抓取失败保护：连续失败次数与暂停截止时间（达到阈值后临时跳过该源）
    failed_fetches: Mapped[int] = mapped_column(Integer, default=0)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    feed_items: Mapped[list[FeedItem]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "xueqiu_account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    xueqiu_account_id: Mapped[int] = mapped_column(ForeignKey("xueqiu_accounts.id", ondelete="CASCADE"), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_mode: Mapped[str] = mapped_column(String(16), default="immediate")
    ai_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    delivery_channel: Mapped[str] = mapped_column(String(32), default="both")
    keywords_include: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    keywords_exclude: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    # 强提醒关键词：命中任一词时无视推送模式立即推送，并在群消息中 @所有人（飞书）
    alert_keywords: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    filter_min_length: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    account: Mapped[XueqiuAccount] = relationship(back_populates="subscriptions")


class FeedItem(Base):
    __tablename__ = "feed_items"
    __table_args__ = (UniqueConstraint("xueqiu_account_id", "item_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    xueqiu_account_id: Mapped[int] = mapped_column(ForeignKey("xueqiu_accounts.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[XueqiuAccount] = relationship(back_populates="feed_items")
    deliveries: Mapped[list[Delivery]] = relationship(back_populates="feed_item", cascade="all, delete-orphan")


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("feed_item_id", "user_id"),
        Index("ix_deliveries_pending", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_item_id: Mapped[int] = mapped_column(ForeignKey("feed_items.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    delivery_mode: Mapped[str] = mapped_column(String(16), default="immediate")
    # 双通道独立状态：解决邮件成功而 Webhook 失败时重复发邮件的问题
    email_status: Mapped[str | None] = mapped_column(String(16), default="pending")
    webhook_status: Mapped[str | None] = mapped_column(String(16), default="pending")
    # 强提醒标记：命中订阅 alert_keywords 的动态，推送时加急并 @所有人
    is_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)

    feed_item: Mapped[FeedItem] = relationship(back_populates="deliveries")
    user: Mapped[User] = relationship()


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="chat_messages")
