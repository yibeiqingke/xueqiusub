from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.htmlsafe import escape_text
from app.models import AiSummaryCache, FeedItem, User
from app.llm_client import resolve_llm_channels, _build_client

settings = get_settings()
logger = logging.getLogger("app.services.digest")

def clean_xueqiu_content_for_summary(author: str, content: str) -> str:
    """雪球引用文本净化去重：清洗回复内容，剥离末尾反复重复的主贴正文，只保留最新回复与原帖提要。
    可节省 50%~70% 的无意义冗余，避免单帖被多次引用刷爆上下文。"""
    clean = re.sub(r"<[^>]+>", "", content or "").replace("&nbsp;", " ").replace("\xa0", " ").strip()
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    if "//@" in clean:
        parts = clean.split("//@", 1)
        my_reply = parts[0].strip()
        quoted = "//@" + parts[1].strip()
        m = re.match(r"^//@([^:]+):\s*(?:回复@[^:]+:\s*)?(.*?)(?=" + re.escape(author) + r":|//@|$)", quoted, re.DOTALL)
        if m:
            target_user = m.group(1).strip()
            user_msg = m.group(2).strip()
            if len(user_msg) > 80:
                user_msg = user_msg[:80] + "..."
            if user_msg:
                return f"{my_reply}\n[引用 @{target_user}: {user_msg}]"
            else:
                return my_reply
        else:
            return my_reply
    return clean


def summarize_items_with_llm(items: list[FeedItem], user: User | None = None, db: Session | None = None, period_label: str = "今天") -> str | None:
    """调用大模型对动态列表进行智能提炼与归纳总结（支持专属大模型通道与智能去重共享缓存）。"""
    from app import appconfig
    from app.models import AiSummaryCache
    settings = get_settings()

    # 1. 判定是否启用大模型总结
    # 优先遵循用户自身设定（如明确禁用则跳过），未特别配置时遵循系统全局开关
    user_enabled = user.llm_enabled if user else None
    if user_enabled is False:
        return None

    global_enabled_str = str(appconfig.get_cfg("llm_enabled", str(settings.llm_enabled))).lower()
    global_enabled = global_enabled_str in ("true", "1", "yes", "on")
    if user_enabled is None and not global_enabled:
        return None

    # 2. 提取需要总结的正文内容（经雪球引用文本净化去重）
    raw_texts = []
    for item in items:
        author = item.account.display_name if item.account else "用户"
        title = (item.title or "").strip()
        clean_c = clean_xueqiu_content_for_summary(author, item.content or "")
        if clean_c:
            raw_texts.append(f"【{author}】{title}\n{clean_c}")

    if not raw_texts:
        return None

    # 3. 确定大模型通道（统一客户端：用户自定义 > 全局主通道 > 全局备用通道）
    from app.llm_client import resolve_llm_channels, call_llm_chat
    channels = resolve_llm_channels(user)
    if not channels:
        return None
    api_base, _api_key, model = channels[0]

    # 4. 智能缓存判定（同一批动态与相同模型只调用一次，多个订阅同博主的用户共享结果）
    sorted_ids = sorted(str(it.id) for it in items if it.id)
    ids_str = ",".join(sorted_ids)
    cache_key = hashlib.sha256(f"{api_base}:{model}:{ids_str}".encode("utf-8")).hexdigest()

    logger = __import__("logging").getLogger("app.services")
    if db:
        try:
            cached = db.scalar(select(AiSummaryCache).where(AiSummaryCache.cache_key == cache_key))
            if cached and cached.summary_text:
                logger.info("命中 AI 总结共享缓存（key=%s...，共 %d 条动态），跳过重复 API 调用", cache_key[:10], len(items))
                return cached.summary_text
        except Exception as e:
            logger.warning("查询 AI 总结缓存异常: %s", e)

    full_text = "\n\n".join(raw_texts)
    # 大模型原生支持 272k 上下文，放宽至 150000 字符限制，避免截断下午与晚间动态
    max_context_chars = 150000
    if len(full_text) > max_context_chars:
        full_text = full_text[:max_context_chars] + "\n...(后略)"

    prompt = f"""你是一位资深的专业财经分析师与量化投研专家。以下是用户{period_label}订阅的大V发布的全部最新动态与互动回复：

{full_text}

请对上述动态进行深度梳理与专业归纳，输出高价值的结构化投研速览：

## 💡 核心观点与宏观研判
- 提炼 2-5 条大V对宏观政策、大盘走势、行业前景的核心研判，简明扼要。

## ⚠️ 风险思考与操作提示
- 归纳大V提示的风险点与仓位管理建议（包括调仓反思、杠杆纪律、防守策略等）。

要求：
- 专业、客观、精炼，直接输出分析结果，去除口语化客套；
- 严禁输出任何形式的多空比例/情绪晴雨表等主观猜测内容；
- 重点信息使用清晰的列表与小标题排版。"""

    content = call_llm_chat(
        messages=[
            {"role": "system", "content": "你是一位专业的财经信息分析助理。"},
            {"role": "user", "content": prompt},
        ],
        max_tokens=10000,
        user=user,
    )
    if not content:
        return None

    # 写入缓存供后续其他用户复用
    if db:
        try:
            db.add(AiSummaryCache(cache_key=cache_key, summary_text=content))
            db.commit()
            logger.info("AI 总结已写入共享缓存（key=%s...）", cache_key[:10])
        except Exception as e:
            db.rollback()
            logger.warning("写入 AI 总结缓存异常: %s", e)

    return content


def render_ai_summary_card(summary_text: str) -> str:
    """将包含情绪晴雨表、调仓雷达和宏观研判的 Markdown 转换为高颜值 HTML 邮件卡片。

    安全约定：``summary_text`` 是大模型回复，而大模型输入是不可信的雪球帖子正文，
    提示注入可以让模型吐出 ``<img onerror=...>`` 之类的钓鱼标记。因此每一段文本在进入
    任何美化步骤（``**bold**`` 正则、【买入】徽章替换）之前 **先 escape_text**，
    输出里出现的每个标签都只能是本函数自己写下的字面量。
    """
    if not summary_text:
        return ""

    lines = summary_text.strip().split("\n")
    html_parts = []

    for line in lines:
        l = line.strip()
        if not l:
            continue

        # 过滤主标题
        if re.match(r"^#\s+.*?(速览|总结|研报|动态)", l, re.I):
            continue

        # 转换二级/三级标题 (## 或 ###)
        header_match = re.match(r"^#{1,3}\s+(.*)", l)
        if header_match:
            # 先转义，再叠加本卡片自己的 <strong>；badge_color 由代码字面量决定
            title = escape_text(header_match.group(1).strip())
            title = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", title)
            badge_color = "#0369a1"
            if "情绪" in title:
                badge_color = "#e11d48"
            elif "调仓" in title or "标的" in title:
                badge_color = "#d97706"
            elif "风险" in title:
                badge_color = "#dc2626"

            html_parts.append(
                f'<div style="font-size:14px;font-weight:700;color:{badge_color};margin:14px 0 6px;padding-bottom:4px;border-bottom:1px dashed #bae6fd;">{title}</div>'
            )
            continue

        # 转换列表项
        list_match = re.match(r"^(?:[-*•]|\d+\.)\s+(.*)", l)
        if list_match:
            item_text = escape_text(list_match.group(1).strip())
            item_text = re.sub(r"\*\*(.+?)\*\*", r'<strong style="color:#0f172a;font-weight:600;">\1</strong>', item_text)
            item_text = item_text.replace("【买入】", '<span style="color:#dc2626;background:#fee2e2;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">买入</span>')
            item_text = item_text.replace("【加仓】", '<span style="color:#dc2626;background:#fee2e2;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">加仓</span>')
            item_text = item_text.replace("【减仓】", '<span style="color:#16a34a;background:#dcfce7;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">减仓</span>')
            item_text = item_text.replace("【止盈】", '<span style="color:#16a34a;background:#dcfce7;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">止盈</span>')
            item_text = item_text.replace("【清仓】", '<span style="color:#16a34a;background:#dcfce7;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">清仓</span>')
            item_text = item_text.replace("【建仓】", '<span style="color:#dc2626;background:#fee2e2;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">建仓</span>')
            item_text = item_text.replace("【观望】", '<span style="color:#64748b;background:#f1f5f9;padding:1px 5px;border-radius:4px;font-weight:600;font-size:12px;">观望</span>')

            html_parts.append(
                f'<div style="margin:6px 0;padding-left:14px;position:relative;font-size:13px;line-height:1.65;color:#334155;">'
                f'<span style="position:absolute;left:2px;color:#0284c7;font-weight:bold;">•</span>'
                f'{item_text}'
                f'</div>'
            )
            continue

        # 普通段落
        p_text = escape_text(l)
        p_text = re.sub(r"\*\*(.+?)\*\*", r'<strong style="color:#0f172a;font-weight:600;">\1</strong>', p_text)
        html_parts.append(
            f'<div style="margin:6px 0;font-size:13px;line-height:1.65;color:#334155;">{p_text}</div>'
        )

    content_html = "".join(html_parts)

    return f"""
    <div style="margin:16px 20px 8px;padding:18px 20px;background:linear-gradient(135deg, #f0fdf4 0%, #f0f9ff 100%);border:1px solid #bae6fd;border-radius:12px;box-shadow:0 2px 8px rgba(14,165,233,0.08);">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;">
        <tr>
          <td width="26" valign="middle">
            <span style="font-size:18px;line-height:1;">🤖</span>
          </td>
          <td valign="middle">
            <span style="font-size:15px;font-weight:700;color:#0369a1;letter-spacing:0.3px;">AI 智能投研内参</span>
            <span style="font-size:11px;color:#0284c7;background:#e0f2fe;padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:600;">核心研判 + 风险提示</span>
          </td>
        </tr>
      </table>
      {content_html}
    </div>"""


