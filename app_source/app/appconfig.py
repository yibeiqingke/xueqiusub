"""DB 支持的配置覆盖。

部分运行期可调项（如每日汇总默认小时、时区）通过 AppConfig 表持久化，
管理员可在后台修改而无需重建容器。读取带短缓存，避免每请求打库。
注意：digest_hour 在本表中仅是「全局默认」，每个用户自己的发送整点存 users.digest_hour。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database import SessionLocal

_CACHE: dict[str, tuple[datetime, Any]] = {}
# 10 秒短缓存：兼顾高频读取性能与跨进程配置生效速度（另一进程修改后最多 10 秒生效）
_CACHE_TTL = timedelta(seconds=10)


def _get_raw(key: str):
    with SessionLocal() as db:
        row = db.scalar(select(AppConfig.value).where(AppConfig.key == key))
        return row


def get_cfg(key: str, default: Any) -> Any:
    now = datetime.now(timezone.utc)
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    raw = _get_raw(key)
    value = raw if raw is not None else default
    _CACHE[key] = (now, value)
    return value


def set_cfg(key: str, value: str) -> None:
    from app.database import Base, engine  # noqa: F401  (确保表已创建)
    with SessionLocal() as db:
        row = db.get(AppConfig, key)
        if row is None:
            db.add(AppConfig(key=key, value=value))
        else:
            row.value = value
        db.commit()
    _CACHE.pop(key, None)


# 汇总小时的唯一取值优先级判定：用户自选值 > 全局默认（后台配置）> .env 默认。
# 所有读取 digest_hour 的地方（worker / notifier / api）都必须走这里，避免门控逻辑散落。


def global_digest_hour() -> int:
    """管理员在后台设置的全局默认汇总小时，供未自选的用户回退使用。
    历史脏值（此前用户设置接口可越界写入 99999 / 0 等）不作为默认生效，直接回落 .env。"""
    from app.config import get_settings

    fallback = int(get_settings().digest_hour)
    try:
        value = int(get_cfg("digest_hour", fallback))
    except (TypeError, ValueError):
        return fallback
    return value if 0 <= value <= 23 else fallback


def digest_hour_for_user(user) -> int:
    """该用户的每日汇总发送整点：users.digest_hour 为空（未自选）或越界时回退全局默认。"""
    value = getattr(user, "digest_hour", None)
    if value is not None:
        try:
            hour = int(value)
        except (TypeError, ValueError):
            hour = None
        if hour is not None and 0 <= hour <= 23:
            return hour
    return global_digest_hour()


# 延迟导入避免循环依赖（models 不依赖本模块）
from app.models import AppConfig  # noqa: E402
