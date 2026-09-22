from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Delivery, FeedItem, Subscription, User, XueqiuAccount

settings = get_settings()
logger = logging.getLogger("app.services.fetcher")

def to_local_datetime_str(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """将 UTC datetime 转换为配置的本地时区字符串 (默认 Asia/Shanghai UTC+8)。"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        from app import appconfig
        tz_name = appconfig.get_cfg("app_timezone", settings.app_timezone)
        return dt.astimezone(ZoneInfo(tz_name)).strftime(fmt)
    except Exception:
        from datetime import timezone as dt_tz, timedelta
        return dt.astimezone(dt_tz(timedelta(hours=8))).strftime(fmt)


def account_feed_url(account: XueqiuAccount) -> str:
    platform = (getattr(account, 'platform', None) or "xueqiu").lower()
    base = settings.rsshub_base_url.rstrip("/")

    if platform == "weibo":
        return f"{base}/weibo/user/{quote(account.xueqiu_user_id, safe='')}"
    elif platform == "cls":
        return f"{base}/cls/telegraph"
    elif platform == "wallstreetcn":
        return f"{base}/wallstreetcn/news/global"
    elif platform == "gelonghui":
        return f"{base}/gelonghui/live"
    elif platform == "36kr":
        return f"{base}/36kr/newsflashes"
    elif platform == "jisilu":
        return f"{base}/jisilu/explore"
    elif platform == "wechat":
        return ""
    elif platform == "xueqiu_cube":
        # 雪球组合调仓动态（cube id 见组合页 URL，如 /soup/12345678 中的数字）
        url = f"{base}/xueqiu/cube/{quote(account.xueqiu_user_id, safe='')}"
        if settings.fetch_limit and settings.fetch_limit > 0:
            url = f"{url}?limit={settings.fetch_limit}"
        return url
    elif platform == "custom_rss":
        return account.xueqiu_user_id.strip()
    else:  # xueqiu
        path = settings.rsshub_xueqiu_path.format(user_id=quote(account.xueqiu_user_id, safe=""))
        if account.feed_type != "all":
            path = f"{path.rstrip('/')}/{quote(account.feed_type, safe='')}"
        url = f"{base}/{path.lstrip('/')}"
        if settings.fetch_limit and settings.fetch_limit > 0:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}limit={settings.fetch_limit}"
        return url


def parse_published(entry) -> datetime | None:
    value = entry.get("published") or entry.get("updated")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def entry_key(entry) -> str:
    raw = entry.get("id") or entry.get("guid") or entry.get("link")
    if raw:
        return str(raw)[:500]
    source = f"{entry.get('title', '')}\n{entry.get('published', '')}\n{entry.get('summary', '')}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()



def is_item_allowed_by_subscription_filter(item: FeedItem, sub: Subscription) -> tuple[bool, str]:
    """检查动态是否符合该订阅用户的降噪与关键词过滤规则。
    返回: (是否放行, 拦截/放行原因)"""
    clean_content = re.sub(r"<[^>]+>", "", item.content or "").strip()
    full_text = f"{item.title or ''} {clean_content}".strip()

    # 1. 最低正文字数门槛（过滤纯表情、纯收到、无意义超短回复）
    min_len = getattr(sub, "filter_min_length", 0) or 0
    if min_len > 0:
        valid_chars_len = len(re.sub(r"[\s\W_]+", "", clean_content))
        if valid_chars_len < min_len:
            return False, f"有效正文字数({valid_chars_len})低于设置门槛({min_len})"

    # 2. 排除关键词黑名单 (命中任一词即拦截)
    kw_exclude = getattr(sub, "keywords_exclude", None)
    if kw_exclude and kw_exclude.strip():
        exclude_words = [w.strip() for w in re.split(r"[,;，；\s]+", kw_exclude) if w.strip()]
        for ew in exclude_words:
            if ew.lower() in full_text.lower():
                return False, f"命中屏蔽关键词: {ew}"

    # 3. 包含关键词白名单 (若配置，必须至少命中其中一个词才放行)
    kw_include = getattr(sub, "keywords_include", None)
    if kw_include and kw_include.strip():
        include_words = [w.strip() for w in re.split(r"[,;，；\s]+", kw_include) if w.strip()]
        if include_words:
            matched = any(iw.lower() in full_text.lower() for iw in include_words)
            if not matched:
                return False, f"未匹配任一关注关键词: {kw_include}"

    return True, "符合过滤规则"


def _validate_custom_rss_url(url: str) -> str:
    """校验管理员自定义 RSS 地址，拒绝回环/私网/保留地址和隐式重定向。"""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
        raise ValueError("自定义 RSS 必须是有效的 HTTP(S) URL")
    if settings.custom_rss_require_https and parsed.scheme.lower() != "https":
        raise ValueError("自定义 RSS 仅允许 HTTPS 地址")
    host = parsed.hostname
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"自定义 RSS 域名无法解析: {host}") from exc
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("自定义 RSS 不允许访问内网或保留地址")
    return url.strip()


def _download_feed(url: str) -> bytes:
    """下载 RSS，禁止自动重定向并限制响应大小。"""
    response = httpx.get(url, timeout=settings.http_timeout_seconds, follow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        raise ValueError("RSS 地址不允许重定向，请填写最终地址")
    response.raise_for_status()
    content_length = response.headers.get("content-length")
    if content_length and int(content_length) > settings.fetch_max_response_bytes:
        raise ValueError("RSS 响应超过大小限制")
    data = response.content
    if len(data) > settings.fetch_max_response_bytes:
        raise ValueError("RSS 响应超过大小限制")
    return data


def _split_keywords(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [w.strip() for w in re.split(r"[,;，；\s]+", raw) if w.strip()]


def is_item_alert_hit(item: FeedItem, sub: Subscription) -> bool:
    """强提醒关键词：命中任一词则该条动态立即推送并在群消息中 @所有人。"""
    words = _split_keywords(getattr(sub, "alert_keywords", None))
    if not words:
        return False
    clean_content = re.sub(r"<[^>]+>", "", item.content or "").strip()
    full_text = f"{item.title or ''} {clean_content}".lower()
    return any(w.lower() in full_text for w in words)


def fetch_account(db: Session, account: XueqiuAccount) -> int:
    now = datetime.now(timezone.utc)
    if getattr(account, 'platform', None) == "wechat":
        account.initialized = True
        account.last_checked_at = now
        db.commit()
        return 0
    try:
        url = account_feed_url(account)
        if (getattr(account, "platform", None) or "").lower() == "custom_rss":
            url = _validate_custom_rss_url(url)
        last_exc = None
        response = None
        # 针对雪球/RSSHub 偶发 502/503 限流或瞬时超时，内置 2 次重试机制
        for attempt in range(2):
            try:
                response = _download_feed(url)
                if attempt == 0 and len(response) == 0:
                    time.sleep(2.5)
                    continue
                break
            except Exception as e:
                last_exc = e
                if attempt == 0:
                    time.sleep(2.5)
                else:
                    raise last_exc

        feed = feedparser.parse(response)
        if feed.bozo and not feed.entries:
            raise RuntimeError(str(feed.bozo_exception))
        if len(feed.entries) > settings.fetch_max_entries:
            raise ValueError(f"RSS 条目数超过限制（{settings.fetch_max_entries}）")

        first_fetch = not account.initialized
        # 批量查重：一次性取出本批 entry 的 key，用 IN 查询判断已存在项，避免逐条 SELECT
        entries = list(feed.entries)
        keys = [entry_key(e) for e in entries]
        existing_keys = set()
        if keys:
            rows = db.scalars(
                select(FeedItem.item_key).where(
                    FeedItem.xueqiu_account_id == account.id,
                    FeedItem.item_key.in_(keys),
                )
            ).all()
            existing_keys = set(rows)
        new_items: list[FeedItem] = []
        seen_keys: set[str] = set(existing_keys)
        for entry in entries:
            key = entry_key(entry)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            published = parse_published(entry) or now  # 缺失发布时间时回退到抓取时间
            item = FeedItem(
                account=account,
                item_key=key,
                title=str(entry.get("title") or "大V新动态"),
                content=str(entry.get("summary") or entry.get("description") or ""),
                link=str(entry.get("link") or ""),
                published_at=published,
            )
            db.add(item)
            new_items.append(item)

        db.flush()
        guard_warning = None
        if not first_fetch:
            # C) 异常守卫：非首次抓取时若新条目数远超阈值，疑似 RSSHub 改版导致刷屏，
            # 仅入库动态、不生成投递，避免对订阅用户狂轰滥炸；告警保留到 last_error 供后台查看。
            if len(new_items) > settings.max_new_items_per_fetch:
                logger.warning(
                    "源 %s 本次新增 %d 条（>%d），疑似异常，已跳过投递生成",
                    account.display_name, len(new_items), settings.max_new_items_per_fetch,
                )
                guard_warning = (
                    f"抓取异常守卫：本次新增 {len(new_items)} 条超过阈值 "
                    f"{settings.max_new_items_per_fetch}，已跳过邮件推送，请检查 RSSHub 路由。"
                )
            else:
                subscriptions = db.scalars(
                    select(Subscription)
                    .join(User)
                    .where(
                        Subscription.xueqiu_account_id == account.id,
                        Subscription.is_enabled.is_(True),
                        User.is_enabled.is_(True),
                        User.email_verified.is_(True),
                    )
                ).all()
                for item in new_items:
                    # 历史冷数据守卫：若动态发布时间超过 3 天，视为 RSSHub 历史回溯冷数据，仅入库留档，不生成即时推送
                    if item.published_at and item.published_at < (now - timedelta(days=3)):
                        logger.info(
                            "源 %s 发现历史冷数据 [%s] (发布于 %s)，仅静默入库，跳过即时推送",
                            account.display_name, (item.title or "")[:25], item.published_at,
                        )
                        continue
                    for subscription in subscriptions:
                        allowed, reason = is_item_allowed_by_subscription_filter(item, subscription)
                        if not allowed:
                            logger.info("用户 %s 订阅过滤拦截动态 [%s]: %s", subscription.user_id, (item.title or "")[:20], reason)
                            continue
                        # 强提醒：命中 alert_keywords 时无视推送模式立即投递并标记 is_alert
                        alert_hit = is_item_alert_hit(item, subscription)
                        db.add(Delivery(
                            feed_item_id=item.id,
                            user_id=subscription.user_id,
                            delivery_mode="immediate" if alert_hit else subscription.delivery_mode,
                            is_alert=alert_hit,
                        ))

        account.initialized = True
        account.last_checked_at = now
        account.failed_fetches = 0
        account.paused_until = None
        # 成功抓取时重置失败计数；异常守卫告警必须保留，不能被无条件清空
        account.last_error = guard_warning
        db.commit()
        return 0 if first_fetch else len(new_items)
    except Exception as exc:
        db.rollback()
        current = db.get(XueqiuAccount, account.id)
        if current:
            current.last_checked_at = now
            current.last_error = str(exc)[:2000]
            current.failed_fetches = (current.failed_fetches or 0) + 1
            # 连续失败达阈值后暂停该源，指数退避封顶
            if current.failed_fetches >= settings.max_consecutive_fetch_errors:
                backoff = min(
                    settings.fetch_error_backoff_seconds * (2 ** (current.failed_fetches - settings.max_consecutive_fetch_errors)),
                    settings.fetch_error_backoff_max_seconds,
                )
                current.paused_until = now + timedelta(seconds=backoff)
        db.commit()
        raise



