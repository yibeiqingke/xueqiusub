from datetime import datetime, timezone
import pytest
from app.models import FeedItem, Subscription
from app.services.fetcher import (
    _split_keywords,
    is_item_alert_hit,
    is_item_allowed_by_subscription_filter,
)
from app.services.notifier import is_auto_truncated_title

def test_split_keywords():
    assert _split_keywords(None) == []
    assert _split_keywords("") == []
    assert _split_keywords("茅台, 腾讯  阿里") == ["茅台", "腾讯", "阿里"]
    assert _split_keywords("宁德时代，比亚迪；中芯国际") == ["宁德时代", "比亚迪", "中芯国际"]

def test_is_auto_truncated_title():
    assert is_auto_truncated_title(None, "正文内容") is True
    assert is_auto_truncated_title("", "正文内容") is True
    assert is_auto_truncated_title("雪球新动态", "正文内容") is True
    assert is_auto_truncated_title("新动态", "正文内容") is True
    assert is_auto_truncated_title("这是一个非常完整且独立的好标题", "文章的正文内容...") is False

def test_is_item_alert_hit():
    sub = Subscription(user_id=1, xueqiu_account_id=1)
    sub.alert_keywords = "大跌, 暴涨"
    item = FeedItem(
        xueqiu_account_id=1,
        item_key="test-1",
        title="今日大盘暴涨解读",
        content="今天各大指数全面大涨",
        link="https://xueqiu.com/1/1",
        published_at=datetime.now(timezone.utc),
    )
    assert is_item_alert_hit(item, sub) is True

    item2 = FeedItem(
        xueqiu_account_id=1,
        item_key="test-2",
        title="日常随笔",
        content="今天天气不错",
        link="https://xueqiu.com/1/2",
        published_at=datetime.now(timezone.utc),
    )
    assert is_item_alert_hit(item2, sub) is False

def test_subscription_keyword_filter():
    sub = Subscription(user_id=1, xueqiu_account_id=1)
    sub.keywords_include = "AI, 人工智能"

    item_hit = FeedItem(
        xueqiu_account_id=1,
        item_key="test-ai",
        title="关于AI大模型的思考",
        content="大模型发展非常迅猛",
        link="https://xueqiu.com/1/3",
        published_at=datetime.now(timezone.utc),
    )
    allowed, reason = is_item_allowed_by_subscription_filter(item_hit, sub)
    assert allowed is True

    item_miss = FeedItem(
        xueqiu_account_id=1,
        item_key="test-other",
        title="传统地产行业现状",
        content="今日地产股波动较大",
        link="https://xueqiu.com/1/4",
        published_at=datetime.now(timezone.utc),
    )
    allowed, reason = is_item_allowed_by_subscription_filter(item_miss, sub)
    assert allowed is False
    assert "未匹配" in reason

    # 排除关键词过滤
    sub_exclude = Subscription(user_id=1, xueqiu_account_id=1)
    sub_exclude.keywords_exclude = "广告, 推广"
    item_ad = FeedItem(
        xueqiu_account_id=1,
        item_key="test-ad",
        title="限时推广活动",
        content="点击链接领取专属福利",
        link="https://xueqiu.com/1/5",
        published_at=datetime.now(timezone.utc),
    )
    allowed, reason = is_item_allowed_by_subscription_filter(item_ad, sub_exclude)
    assert allowed is False
    assert "命中屏蔽关键词" in reason
