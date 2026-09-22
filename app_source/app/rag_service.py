"""专属大V知识库智能投研问答与个股雷达引擎（RAG）。"""
import re
import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.orm import Session, joinedload

from app.models import FeedItem, Subscription, User, XueqiuAccount
from app.config import get_settings
from app.security import decrypt_secret
from app import appconfig

logger = logging.getLogger("app.rag")



def to_local_datetime_str(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """将 UTC datetime 转换为配置的本地时区字符串 (默认 Asia/Shanghai UTC+8)。"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        from app import appconfig
        tz_name = appconfig.get_cfg("app_timezone", get_settings().app_timezone)
        return dt.astimezone(ZoneInfo(tz_name)).strftime(fmt)
    except Exception:
        from datetime import timezone as dt_tz, timedelta
        return dt.astimezone(dt_tz(timedelta(hours=8))).strftime(fmt)


def extract_keywords(q: str) -> List[str]:
    """从中文/英文投研提问中提取高价值的搜索关键词。"""
    stopwords = {
        "买股票的老木匠", "老木匠", "最近", "近期", "有什么", "变化", "态度", "看法", 
        "观点", "请问", "帮我", "汇总", "分析", "如何", "怎么看", "历史", "所有",
        "的大", "大盘", "什么", "以及", "关于", "对于", "请详细", "动态", "发帖", "梳理"
    }
    
    codes = re.findall(r'[A-Za-z0-9_]{3,}', q)
    brackets = re.findall(r'[【\[（\(](.*?)[】\]）\)]', q)
    
    clean_q = q
    for sw in stopwords:
        clean_q = clean_q.replace(sw, " ")
    
    raw_words = [w.strip() for w in re.split(r'[\s，。？！、\?,\.\!]+', clean_q) if len(w.strip()) >= 2]
    
    kws = set(codes + brackets + raw_words)
    for w in list(raw_words):
        if len(w) > 3:
            for i in range(len(w) - 1):
                sub2 = w[i:i+2]
                if sub2 not in stopwords:
                    kws.add(sub2)
                if i + 3 <= len(w):
                    sub3 = w[i:i+3]
                    if sub3 not in stopwords:
                        kws.add(sub3)

    # 优先保留有具体股票/资产含义的核心关键词
    clean_kws = [k for k in kws if len(k) >= 2 and k not in stopwords]
    return clean_kws if clean_kws else [q.strip()[:6]]


def _like_escape(keyword: str) -> str:
    """转义 LIKE 通配符，防止用户输入的 %/_ 扰动匹配语义。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def retrieve_relevant_feed_items(db: Session, user: User, query: str, top_k: int = 15) -> List[FeedItem]:
    """根据用户提问，在用户关注的大V历史发帖中检索最相关的动态。"""
    sub_account_ids = db.scalars(
        select(Subscription.xueqiu_account_id).where(
            Subscription.user_id == user.id,
            Subscription.is_enabled.is_(True),
        )
    ).all()

    if not sub_account_ids:
        # 如果当前用户还没有订阅，默认在全部账号中检索
        sub_account_ids = db.scalars(select(XueqiuAccount.id)).all()

    if not sub_account_ids:
        return []

    q = query.strip()
    keywords = extract_keywords(q)

    # 检查是否有特定博主名称过滤
    target_account_ids = sub_account_ids
    accounts = db.scalars(select(XueqiuAccount).where(XueqiuAccount.id.in_(sub_account_ids))).all()
    for acc in accounts:
        if acc.display_name in q or (acc.display_name[:3] in q if len(acc.display_name) >= 3 else False):
            target_account_ids = [acc.id]
            break

    # 构造模糊检索条件
    filters = []
    for kw in keywords[:8]:
        pattern = f"%{_like_escape(kw)}%"
        filters.append(FeedItem.title.ilike(pattern, escape="\\"))
        filters.append(FeedItem.content.ilike(pattern, escape="\\"))

    stmt = (
        select(FeedItem)
        .options(joinedload(FeedItem.account))
        .where(
            FeedItem.xueqiu_account_id.in_(target_account_ids),
            or_(*filters) if filters else True,
        )
        .order_by(FeedItem.published_at.desc(), FeedItem.id.desc())
        .limit(top_k * 3)
    )

    candidates = db.scalars(stmt).unique().all()
    if not candidates:
        # 如果严格匹配为空，扩大到最近的全部发帖
        stmt = (
            select(FeedItem)
            .options(joinedload(FeedItem.account))
            .where(FeedItem.xueqiu_account_id.in_(target_account_ids))
            .order_by(FeedItem.published_at.desc(), FeedItem.id.desc())
            .limit(top_k)
        )
        candidates = db.scalars(stmt).unique().all()

    def score_item(item: FeedItem) -> float:
        content = f"{item.title} {item.content}"
        matches = sum(content.count(kw) for kw in keywords)
        recency = item.published_at.timestamp() if item.published_at else 0
        return matches * 1000000 + (recency / 100000)

    scored = sorted(candidates, key=score_item, reverse=True)
    return scored[:top_k]


def ask_financial_copilot(db: Session, user: User, query: str) -> Dict[str, Any]:
    """专属投研智能问答：结合 RAG 检索上下文与大模型生成专业分析。"""
    from app.llm_client import resolve_llm_channels, call_llm_chat

    if not resolve_llm_channels(user):
        return {
            "answer": "未配置大模型 API，请先在后台或推送设置中配置大模型 API Key。",
            "sources": [],
        }

    items = retrieve_relevant_feed_items(db, user, query, top_k=10)
    if not items:
        return {
            "answer": f"在您关注的大V历史动态中，未检索到与「**{query}**」直接相关的发帖记录。\n\n建议在首页添加更多相关领域的财经博主后再试。",
            "sources": [],
        }

    context_chunks = []
    sources = []
    for idx, it in enumerate(items, start=1):
        author = it.account.display_name if it.account else "大V"
        pub_time = to_local_datetime_str(it.published_at, "%Y-%m-%d %H:%M") or "近期"
        clean_text = re.sub(r"<[^>]+>", "", it.content or it.title or "").replace("&nbsp;", " ").strip()
        if len(clean_text) > 450:
            clean_text = clean_text[:450] + "..."

        context_chunks.append(f"【参考材料[{idx}]】博主：{author} | 发布时间：{pub_time}\n内容：{clean_text}")
        sources.append({
            "index": idx,
            "author": author,
            "title": (it.title or "动态").strip(),
            "time": pub_time,
            "link": it.link,
            "snippet": clean_text[:120],
        })

    context_str = "\n\n".join(context_chunks)

    prompt = f"""你是一位顶级买方机构资深投资顾问与财经投研助理。
以下是从用户关注的雪球/微博/快讯大V历史发帖中检索出的真实材料：

{context_str}

---
【用户提问】：{query}

请基于上述检索到的材料，对用户的提问进行严谨、深度、结构化的解答：
1. 💡【核心结论与观点提炼】：正面回答用户问题，总结大V们的共识、分歧或核心态度；
2. ⏳【观点演化与时间脉络】：梳理大V在不同时间节点对该问题/标的判断的变化轨迹；
3. 🎯【涉及标的与调仓信号】：若涉及具体股票/行业，说明操作动作（买入/加仓/减仓/止盈）与核心理由；
4. ⚠️【风险提示与逻辑盲区】：提示相关风险或不确定性。

要求：
- 严格依据提供的发帖材料回答，不要凭空捏造发帖记录；
- 引用具体观点时标注出处编号（如 [1]、[2]）；
- 语言专业、精炼、富有洞察力。"""

    answer = call_llm_chat(
        messages=[
            {"role": "system", "content": "你是一位资深专业的买方财经投研分析师。"},
            {"role": "user", "content": prompt},
        ],
        max_tokens=1200,
        user=user,
    )
    if not answer:
        logger.warning("RAG 问答调用失败（所有通道）")
        return {
            "answer": "调用大模型分析失败，请稍后重试。",
            "sources": sources,
        }
    return {
        "answer": answer,
        "sources": sources,
    }
