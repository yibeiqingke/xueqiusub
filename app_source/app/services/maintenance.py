from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Delivery, FeedItem

settings = get_settings()
logger = logging.getLogger("app.services.maintenance")

def purge_old_data(db: Session) -> dict:
    """F) 定期清理：删除过期的 feed_item 与 delivery，避免 SQLite 无限膨胀。

    返回被删除的各表行数，便于日志输出。仅清理“已发送/失败且超过保留期”的投递，
    以及超过保留期的动态（动态删除会级联清理其 delivery）。
    """
    now = datetime.now(timezone.utc)
    feed_cutoff = now - timedelta(days=settings.feed_item_retention_days)
    delivery_cutoff = now - timedelta(days=settings.delivery_retention_days)

    # 先删过期且终态的 delivery（pending/retry 不删以免丢投递）：
    # sent 按实际发送时间 sent_at 判定；failed 按最后一次失败调度时间 next_attempt_at 判定
    delivery_del = db.execute(
        delete(Delivery).where(
            or_(
                and_(Delivery.status == "sent", Delivery.sent_at.is_not(None), Delivery.sent_at <= delivery_cutoff),
                and_(Delivery.status == "failed", Delivery.next_attempt_at <= delivery_cutoff),
            )
        )
    )
    # 再删过期 feed_item，但排除仍有关联 pending/retry 投递的条目，避免级联误删待发邮件
    active_item_ids = select(Delivery.feed_item_id).where(
        Delivery.status.in_(("pending", "retry"))
    )
    item_del = db.execute(
        delete(FeedItem).where(
            FeedItem.published_at <= feed_cutoff,
            FeedItem.id.not_in(active_item_ids),
        )
    )
    db.commit()
    return {
        "deliveries": delivery_del.rowcount,
        "feed_items": item_del.rowcount,
    }

