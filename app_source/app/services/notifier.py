from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.htmlsafe import (
    SEG_QUOTE,
    ImageRun,
    escape_text,
    href_host,
    html_to_segments,
    html_to_text,
    one_line,
    safe_href,
)
from app.mailer import send_email, user_smtp_config
from app.models import Delivery, DigestLog, FeedItem, Subscription, User, XueqiuAccount
from app.security import make_token
from .fetcher import to_local_datetime_str
from .digest import clean_xueqiu_content_for_summary, render_ai_summary_card, summarize_items_with_llm

try:
    import fcntl
except ImportError:
    fcntl = None

settings = get_settings()
logger = logging.getLogger("app.services.notifier")

# 邮件排版样式常量：全部是代码字面量，不可被帖子内容影响。
_SPACER_DIV = '<div style="height:10px;"></div>'
_LINK_STYLE = "color:#2563eb;text-decoration:none;font-weight:500;"
_PILL_STYLE = ("color:#2563eb;background:#eff6ff;padding:2px 6px;border-radius:4px;"
               "text-decoration:none;font-weight:500;font-size:13.5px;")
_QUOTE_STYLE = ("margin:12px 0;padding:10px 14px;background:#f8fafc;"
                "border-left:3px solid #6366f1;border-radius:0 6px 6px 0;"
                "color:#475569;font-size:13.5px;line-height:1.65;")
# 雪球同图多尺寸：原图 URL 后拼 !custom.jpg / !800.jpg / !thumb.jpg 作缩略图
_IMAGE_SIZE_SUFFIX_RE = re.compile(r"!(?:custom|800|thumb|\d+)\.jpg$", re.I)
# 连续空行折叠（与旧实现一致）：图片被抽去相册后，原先围绕它的 <br> 会连成一片
_REPEATED_BREAK_RE = re.compile(r"(?:<br>\s*){2,}")
# 相册前置的收尾：只可能是本模块自己写的 <br>/间距 div 之后的空白
_TRAILING_BREAK_RE = re.compile(r"(?:<br>|\s)+$")


def unsubscribe_url(user_id: int) -> str:
    token = make_token(user_id, "unsubscribe")
    return f"{settings.base_url.rstrip('/')}/unsubscribe/{token}"


def is_auto_truncated_title(title: str | None, content: str | None) -> bool:
    if not title or not title.strip():
        return True
    t = title.strip()
    if t in ("雪球新动态", "雪球动态", "新动态"):
        return True
    clean_content = re.sub(r"<[^>]+>", "", content or "").replace("&nbsp;", " ").replace("\xa0", " ").strip()
    prefix = t.rstrip(".").rstrip("…").strip()
    if not prefix:
        return True
    if clean_content.startswith(prefix):
        return True
    if prefix in clean_content[:max(len(prefix) + 30, 120)]:
        return True
    return False


def extract_item_snippet(item: FeedItem, max_len: int = 32) -> str:
    """从 FeedItem 提炼用于邮件标题的精炼文本（长文标题 / 纯净首句观点）。

    返回值**保证是单行**：邮件 Subject 经 compat32 头编码，RSS 标题里夹一个
    ``\\r\\n`` 就可能多出一个邮件头（BCC / 内容注入），所以进 Subject 前一律压行。
    """
    title = (item.title or "").strip()
    content = item.content or ""
    
    # 如果是真正的长文/专栏独立标题
    if not is_auto_truncated_title(title, content):
        clean_t = one_line(title)
        if len(clean_t) > max_len:
            clean_t = clean_t[:max_len].rstrip() + "..."
        return f"《{clean_t}》"
    
    raw_text = content or title
    # 去除 blockquote（引用的旧帖/被回复帖）
    text = re.sub(r"<blockquote.*?>.*?</blockquote>", "", raw_text, flags=re.DOTALL)
    # HTML → 纯文本（标准库解析器处理标签/实体/注释，比 <[^>]+> 正则更不易被畸形标记骗过）
    text = html_to_text(text)
    # 去除表情标签 如 [捂脸]
    text = re.sub(r"\[[^\]]+\]", "", text)
    # 去除 //@xxx: 转发链及后续引用
    text = re.sub(r"//@[^:]+:[^ \n\r]*", " ", text)
    # 去除 回复@xxx:
    text = re.sub(r"回复@[^:]+:[^ \n\r]*", " ", text)
    # 去除 财联社/快讯时间戳 如 03:52:23财联社9月1日电，
    text = re.sub(r"^\d{1,2}:\d{2}(:\d{2})?.*?电[，,]\s*", "", text.strip())
    # 去除尾部 APP 署名水印 如 (来自财联社APP) 或 （来自...APP）
    text = re.sub(r"[\(（][^()（）]*?(?:APP|客户端)[^)）]*?[\)）]", "", text)
    # 压缩连续空白并强制单行（CR/LF/VT/零宽字符都不可入 Subject）
    text = one_line(text)
    
    if not text:
        return ""
    
    # 优先在第一个完整句子标点（。！？!?）处截断
    m = re.search(r"^(.+?[。！？!?])", text)
    if m and 10 <= len(m.group(1)) <= max_len + 6:
        return m.group(1).rstrip(" ")
    
    # 如果有分号截断，去除末尾分号
    m_semi = re.search(r"^(.+?[；;])", text)
    if m_semi and 10 <= len(m_semi.group(1)) <= max_len + 6:
        return m_semi.group(1).rstrip("；; ")

    if len(text) > max_len:
        return text[:max_len].rstrip(" ，,。！？!?；;") + "..."
    return text.rstrip("；;，, ")


def build_immediate_merged_subject(deliveries: list[Delivery]) -> str:
    """构建多条合并即时邮件标题。
    例如：
      单作者: [智投·买股票的老木匠(4条)] 凯文沃什在杰克逊霍尔年会上的讲话...
      多作者: [智投(4条)] 买股票的老木匠、段永平 · 凯文沃什在年会上...
    """
    total = len(deliveries)
    if not deliveries:
        return "[智投内参] 新动态"
    prefix = "🚨" if any(getattr(d, "is_alert", False) for d in deliveries) else ""

    authors = sorted({one_line(dv.feed_item.account.display_name) for dv in deliveries if dv.feed_item and dv.feed_item.account})
    # 取最新发布的动态提炼核心看点
    latest_delivery = max(
        deliveries,
        key=lambda d: ((d.feed_item.published_at.replace(tzinfo=None) if getattr(d.feed_item.published_at, "tzinfo", None) else d.feed_item.published_at) if (d.feed_item and d.feed_item.published_at) else datetime.min),
    )
    snippet = extract_item_snippet(latest_delivery.feed_item, max_len=30)

    if len(authors) == 1:
        author = authors[0]
        if snippet:
            return f"{prefix}[智投·{author}({total}条)] {snippet}"
        return f"{prefix}[智投·{author}] 新动态（共 {total} 条）"
    else:
        author_str = "、".join(authors[:2])
        if len(authors) > 2:
            author_str += f"等{len(authors)}人"
        if snippet:
            return f"{prefix}[智投({total}条)] {author_str} · {snippet}"
        return f"{prefix}[智投内参] {author_str} 等 {total} 条新动态"


def build_single_delivery_subject(delivery: Delivery) -> str:
    """构建单条动态即时邮件标题。
    例如：
      [智投·买股票的老木匠] 《半导体产业链半年度投资体检》
      [智投·买股票的老木匠] 凯文沃什在杰克逊霍尔年会上的讲话...
    """
    author = one_line(delivery.feed_item.account.display_name) if (delivery.feed_item and delivery.feed_item.account) else "智投内参"
    snippet = extract_item_snippet(delivery.feed_item, max_len=35)
    prefix = "🚨" if getattr(delivery, "is_alert", False) else ""
    if snippet:
        return f"{prefix}[智投·{author}] {snippet}"
    return f"{prefix}[智投内参] {author} 有新动态"


def build_daily_digest_subject(batch: list[Delivery], local_date, batch_idx: int, total_batches: int) -> str:
    """构建每日汇总邮件标题。
    例如：
      [智投日报 09-01] 买股票的老木匠(4条) · 凯文沃什在年会上...
    """
    total = len(batch)
    date_str = f"{local_date:%m-%d}"
    
    author_counts: dict[str, int] = {}
    for d in batch:
        if d.feed_item and d.feed_item.account:
            name = one_line(d.feed_item.account.display_name)
            author_counts[name] = author_counts.get(name, 0) + 1
    
    sorted_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_authors) == 1:
        author_summary = f"{sorted_authors[0][0]}({total}条)"
    elif len(sorted_authors) <= 2:
        author_summary = "、".join(f"{name}({cnt}条)" for name, cnt in sorted_authors)
    else:
        author_summary = f"{sorted_authors[0][0]}等{len(sorted_authors)}人({total}条)"
    
    latest_delivery = max(
        batch,
        key=lambda d: ((d.feed_item.published_at.replace(tzinfo=None) if getattr(d.feed_item.published_at, "tzinfo", None) else d.feed_item.published_at) if (d.feed_item and d.feed_item.published_at) else datetime.min),
    )
    snippet = extract_item_snippet(latest_delivery.feed_item, max_len=26)
    
    part_suffix = f" · {batch_idx + 1}/{total_batches}" if total_batches > 1 else ""
    
    if snippet:
        return f"[智投日报 {date_str}] {author_summary} · {snippet}{part_suffix}"
    return f"[智投日报 {date_str}] {author_summary}{part_suffix}"


def format_email_content(raw_content: str, base_url: str = "") -> str:
    """把雪球帖子的**上游 HTML** 渲染成邮件正文片段。

    旧实现是 147 行 ``re.sub`` 补丁：在原样入库的 feed HTML 上改写表情、图片、
    引用块、股票代码胶囊、普通链接……但它始终在"搬运上游字节"，所以
    ``href="javascript:…"``、``on*`` 属性、嵌套标签绕过（``<scri<scr<script>ipt>``）、
    注释绕过都能活着走到订户收件箱。

    新实现不再修补上游标记：用标准库 ``html.parser`` 把 feed HTML 解析成
    文本/链接/图片三类"事实"（:func:`app.htmlsafe.html_to_segments`），
    然后**只由本模块自己的字面量**重新拼出 HTML：

    * 文字：先 :func:`plain_text_to_html`（转义 → ``\\n\\n`` 间距 div / ``\\n`` → ``<br>``）；
    * ``<a>``：仅保留链接文字与 href，href 过 :func:`app.htmlsafe.safe_href`
      （http/https/mailto 白名单，其余降级为 ``#``）——**股票代码链接等有价值的
      站内链接仍然可点**，``xq_stock`` 仍渲染成胶囊；
    * ``<blockquote>``：仍渲染成左侧色条引用盒；
    * 表情 ``<img alt="[捂脸]">``：仍还原成 ``[捂脸]`` 文字；
    * 正文配图：仍按主帖/引用帖分别排成相册（1 张铺满、2/4 张双列、其余三列），
      ``!custom.jpg`` 之类缩略图与原图去重；但 src 与外层链接同样过
      :func:`app.htmlsafe.safe_href`，非 http/https 的图片一律不出图，
      上游写在 ``<img>`` 上的 style/宽高/on* 属性全部丢弃。

    ``base_url`` 传帖子自身链接，用于把 ``//xueqiu.com/…``、``/H600519``
    这类相对地址还原成绝对地址（还原后仍会重判协议）。
    """
    if not raw_content:
        return ""
    return segments_to_email_html(html_to_segments(raw_content), base_url=base_url)


def plain_text_to_html(text: str) -> str:
    """纯文本 → 邮件 HTML：**先转义，后换行**。

    顺序很重要：先 ``escape_text`` 保证所有尖括号都变成实体，再把我们自己的
    字面量（``<br>`` / 间距 div）替换进换行符，因此输出里的标签一定出自本模块。
    """
    normalized = re.sub(r"\r\n?", "\n", text or "")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    escaped = escape_text(normalized)
    return escaped.replace("\n\n", _SPACER_DIV).replace("\n", "<br>")


def segments_to_email_html(segments, base_url: str = "") -> str:
    """把 :func:`app.htmlsafe.html_to_segments` 的片段序列渲染成邮件 HTML。

    配图按片段（主帖 / 引用帖各一段）汇总成相册，与旧实现的分栏规则一致：
    1 张铺满、2/4 张双列、其余三列；引用帖整体缩小以示意"这是转发的旧帖"。
    """
    out: list[str] = []
    for segment in segments:
        srcs: list[str] = []
        parts: list[str] = []
        for run in segment.runs:
            if isinstance(run, ImageRun):
                srcs.extend(run.srcs)
            else:
                parts.append(_run_to_html(run, base_url))
        inner = _REPEATED_BREAK_RE.sub("<br>", "".join(parts))
        is_quote = segment.kind == SEG_QUOTE
        gallery = _gallery_html(srcs, base_url=base_url, is_quote=is_quote)
        if srcs:
            # 图片原先靠 <br>/空行占位，剥离后正文尾部会多出一条分隔线，先收掉再接相册
            inner = _TRAILING_BREAK_RE.sub("", inner)
        body = inner + gallery
        if not body:
            continue
        if is_quote:
            out.append(f'<div style="{_QUOTE_STYLE}">{body}</div>')
        else:
            out.append(body)
    return "".join(out)


def _gallery_html(srcs: list[str], base_url: str, is_quote: bool) -> str:
    """渲染配图相册。src 与外层链接都过 safe_href，非 http/https 的直接不出图。"""
    urls: list[str] = []
    seen: set[str] = set()
    for src in srcs:
        # 雪球同图多尺寸：缩略图带 !custom.jpg / !800.jpg 等后缀，取原图地址去重
        clean = _IMAGE_SIZE_SUFFIX_RE.sub("", (src or "").strip())
        href = safe_href(clean, base_url=base_url)
        if href == "#" or href in seen:
            continue
        seen.add(href)
        urls.append(href)
    if not urls:
        return ""

    if len(urls) == 1:
        max_h = "260px" if is_quote else "480px"
        cells = [
            f'<a href="{u}" target="_blank" rel="noopener noreferrer nofollow" '
            f'style="display:inline-block;text-decoration:none;">'
            f'<img src="{u}" style="max-width:100%;max-height:{max_h};width:auto;height:auto;'
            f'border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,0.08);display:block;border:0;" />'
            f'</a>'
            for u in urls
        ]
        return f'<div style="margin:10px 0;text-align:left;">{"".join(cells)}</div>'

    if len(urls) in (2, 4):
        cell_w, max_h = "48%", ("140px" if is_quote else "220px")
    else:
        cell_w, max_h = "31.3%", ("100px" if is_quote else "160px")
    cells = [
        f'<a href="{u}" target="_blank" rel="noopener noreferrer nofollow" '
        f'style="display:inline-block;width:{cell_w};margin:1%;vertical-align:top;'
        f'box-sizing:border-box;text-decoration:none;">'
        f'<img src="{u}" style="width:100%;max-height:{max_h};object-fit:cover;border-radius:6px;'
        f'box-shadow:0 1px 3px rgba(0,0,0,0.06);display:block;border:0;" />'
        f'</a>'
        for u in urls
    ]
    return f'<div style="margin:10px 0;font-size:0;text-align:left;">{"".join(cells)}</div>'


def _run_to_html(run, base_url: str) -> str:
    if isinstance(run, str):
        return plain_text_to_html(run)
    # LinkRun：href 唯一收口；除 href 外不搬运上游任何属性
    href = safe_href(run.href, base_url=base_url)
    text = plain_text_to_html(run.text)
    style = _PILL_STYLE if run.pill else _LINK_STYLE
    host = href_host(run.href)
    external = "" if (not host or host == "xueqiu.com" or host.endswith(".xueqiu.com")) else '<span style="font-size:11px;color:#94a3b8;">↗</span>'
    return f'<a href="{href}" target="_blank" rel="noopener noreferrer nofollow" style="{style}">{text}{external}</a>'


def render_item(item: FeedItem) -> str:
    """单条雪球动态的精致卡片片段，供 wrap_email 组合。"""
    raw_account = (item.account.display_name if item.account else "") or "雪球用户"
    account = escape_text(raw_account)
    title = (item.title or "").strip()
    raw_link = item.link or ""
    link = safe_href(raw_link)
    content = format_email_content(item.content or "", base_url=raw_link)
    # 首字母从**未转义**的名字上切，避免在已转义串上取到 '&' 再转义成 '&amp;'
    initial = escape_text(raw_account.strip()[:1] or "雪")

    # 发布时间格式化 (本地时区转换)
    when = escape_text(to_local_datetime_str(item.published_at, "%m-%d %H:%M"))

    title_html = ""
    if not is_auto_truncated_title(title, item.content):
        title_html = f'<a href="{link}" style="display:block;font-size:16.5px;font-weight:700;color:#0f172a;text-decoration:none;line-height:1.45;margin:12px 0 8px;">{escape_text(title)}</a>'

    return f"""
    <div style="padding:20px 24px;border-bottom:1px solid #f1f5f9;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
        <tr>
          <td width="42" valign="middle" style="padding-right:12px;">
            <div style="width:38px;height:38px;border-radius:50%;background:#e0e7ff;color:#3730a3;font-size:15px;font-weight:700;line-height:38px;text-align:center;">{initial}</div>
          </td>
          <td valign="middle">
            <div style="font-size:14.5px;color:#0f172a;font-weight:600;line-height:1.3;">{account}</div>
            {"<div style='font-size:12px;color:#94a3b8;margin-top:2px;'>" + when + "</div>" if when else ""}
          </td>
        </tr>
      </table>
      {title_html}
      <div style="font-size:14.5px;color:#334155;line-height:1.75;word-break:break-word;">{content}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;">
        <tr>
          <td align="right">
            <a href="{link}" style="display:inline-block;font-size:12.5px;color:#2563eb;background:#eff6ff;padding:5px 12px;border-radius:16px;text-decoration:none;font-weight:500;">查看原帖 ›</a>
          </td>
        </tr>
      </table>
    </div>"""


def wrap_email(inner_html: str, heading: str, subheading: str, unsubscribe_url: str) -> str:
    """统一的高颜值邮件整体外壳：居中卡片 + 深蓝品牌头部 + 底部退订。

    ``heading``/``subheading`` 由调用方拼出，里面会混入博主展示名（来自 feed，
    可被上游影响），因此在插入点转义；退订地址同样要过 ``safe_href``——
    ``BASE_URL`` 若被配成别的协议，也不该变成可点即执行的链接。
    ``inner_html`` 是本模块自己渲染的可信片段，原样嵌入。
    """
    heading = escape_text(heading)
    subheading = escape_text(subheading)
    unsub_href = safe_href(unsubscribe_url)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>智投内参</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 0;">
    <tr><td align="center" style="padding:0 12px;">
      <table role="presentation" width="580" cellpadding="0" cellspacing="0" style="max-width:580px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(15,23,42,0.06);">
        <!-- 品牌深色头部 -->
        <tr><td style="background:linear-gradient(135deg, #0f172a 0%, #1e293b 100%);padding:22px 24px;">
          <div style="font-size:18px;font-weight:700;color:#ffffff;letter-spacing:0.5px;">智投内参</div>
          <div style="font-size:13px;color:#94a3b8;margin-top:4px;">{subheading}</div>
        </td></tr>
        <!-- 摘要/计数提示栏 -->
        <tr><td style="background:#fafbfc;padding:12px 24px;border-bottom:1px solid #f1f5f9;">
          <div style="font-size:13.5px;font-weight:600;color:#475569;">{heading}</div>
        </td></tr>
        <!-- 动态列表内容 -->
        <tr><td style="padding:0;">
          {inner_html}
        </td></tr>
        <!-- 底部退订与声明 -->
        <tr><td style="padding:18px 24px 24px;border-top:1px solid #f1f5f9;background:#fafbfc;text-align:center;">
          <a href="{unsub_href}" style="font-size:12px;color:#94a3b8;text-decoration:none;">取消此订阅</a>
          <div style="font-size:11.5px;color:#cbd5e1;margin-top:6px;">你收到此邮件，是因为在「智投内参」平台开启了相关推送。</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""



def get_user_subscriptions_map(db: Session, user_id: int) -> dict[int, Subscription]:
    """获取指定用户所有已启用的订阅字典: {xueqiu_account_id: Subscription}"""
    subs = db.scalars(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.is_enabled.is_(True),
        )
    ).all()
    return {s.xueqiu_account_id: s for s in subs}


def _delivery_lock_path() -> str:
    """投递互斥锁文件路径：放在数据库所在目录（web 与 worker 共享同一数据卷）。"""
    raw = settings.database_url.split("sqlite:///", 1)[-1]
    return os.path.join(os.path.dirname(os.path.abspath(raw)), ".send_deliveries.lock")


@contextmanager
def _delivery_lock(timeout: float = 90.0):
    """跨进程文件锁：web 与 worker 并发触发投递时串行化，避免同一批 pending 被重复发送。"""
    if fcntl is None:  # 非 POSIX 平台退化为无锁
        yield True
        return
    path = _delivery_lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w")
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except OSError:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
    try:
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def send_immediate_deliveries(db: Session) -> None:
    """即时投递统一入口（跨进程互斥）。"""
    with _delivery_lock() as acquired:
        if not acquired:
            logger.warning("另一进程正在执行即时投递，本次跳过（重试机制稍后会兜底处理）")
            return
        _send_immediate_deliveries_locked(db)


def _send_immediate_deliveries_locked(db: Session) -> None:
    now = datetime.now(timezone.utc)
    deliveries = db.scalars(
        select(Delivery).join(Delivery.feed_item).order_by(FeedItem.published_at.is_(None), FeedItem.published_at.asc())
        .options(joinedload(Delivery.user), joinedload(Delivery.feed_item).joinedload(FeedItem.account))
        .where(
            Delivery.status.in_(("pending", "retry")),
            Delivery.delivery_mode == "immediate",
            Delivery.next_attempt_at <= now,
            Delivery.user.has(and_(User.is_enabled.is_(True), User.email_verified.is_(True))),
        )
        .limit(50)
    ).unique().all()

    if not deliveries:
        return

    # B) 同一用户的多条即时动态合并为一封邮件，避免短时连发被判垃圾邮件
    if settings.immediate_merge_per_user:
        by_user: dict[int, list[Delivery]] = {}
        for d in deliveries:
            by_user.setdefault(d.user_id, []).append(d)
        sent_user_ids = set()
        for user_id, user_deliveries in by_user.items():
            user = user_deliveries[0].user
            subs_map = get_user_subscriptions_map(db, user_id)

            # 精准分流：按订阅通道与双通道子状态决定本轮要发送的内容，
            # 已成功的通道不会重复发送，避免“邮件成功但 Webhook 失败”时重发邮件
            def _channel_of(dv: Delivery) -> str:
                return getattr(subs_map.get(dv.feed_item.xueqiu_account_id), 'delivery_channel', 'both') or 'both'

            email_pending = [
                dv for dv in user_deliveries
                if _channel_of(dv) in ("both", "email", "all") and (dv.email_status or "pending") != "sent"
            ]
            webhook_pending = [
                dv for dv in user_deliveries
                if _channel_of(dv) in ("both", "webhook", "all") and (dv.webhook_status or "pending") != "sent"
            ]

            email_error: Exception | None = None
            webhook_error: Exception | None = None

            # 1. 尝试邮件发送 (仅当有包含邮件通道且未发送成功的数据时发送)
            if email_pending:
                try:
                    title_names = ", ".join(
                        sorted({dv.feed_item.account.display_name for dv in email_pending})
                    )
                    inner = "".join(render_item(dv.feed_item) for dv in email_pending)
                    unsub = unsubscribe_url(user_id)
                    body = wrap_email(
                        inner,
                        heading=f"你有 {len(email_pending)} 条新动态",
                        subheading=f"来自 {title_names}",
                        unsubscribe_url=unsub,
                    )
                    subject = build_immediate_merged_subject(email_pending)
                    cfg = user_smtp_config(user)
                    send_email(
                        user.email,
                        subject,
                        body,
                        config=cfg,
                    )
                    for dv in email_pending:
                        dv.email_status = "sent"
                except Exception as exc:
                    email_error = exc
                    for dv in email_pending:
                        dv.email_status = "failed"
                    logger.warning("即时邮件推送异常 (user=%s): %s", user.email, exc)

            # 2. 尝试群机器人 Webhook 推送（检查返回值，失败同样进入重试）
            if webhook_pending and user.webhook_enabled and user.webhook_url:
                try:
                    from app.webhook import send_user_webhook, render_item_markdown
                    webhook_names = ", ".join(
                        sorted({dv.feed_item.account.display_name for dv in webhook_pending})
                    )
                    cards = [render_item_markdown(dv.feed_item, include_author=True) for dv in webhook_pending]
                    immed_text = "\n\n---\n\n".join(cards)
                    # 每条动态块内已自带“查看原文”链接，底部按钮仅在单条时突出显示
                    first_link = webhook_pending[0].feed_item.link if len(webhook_pending) == 1 else None
                    # 强提醒：命中 alert_keywords 的动态 @所有人（飞书），标题加 🚨 前缀
                    alert_hit = any(getattr(dv, "is_alert", False) for dv in webhook_pending)
                    title = f"实时动态 ({len(webhook_pending)}条) · {webhook_names}"
                    if alert_hit:
                        title = f"🚨【强提醒】{title}"
                    if send_user_webhook(user, title, immed_text, link=first_link, at_all=alert_hit):
                        for dv in webhook_pending:
                            dv.webhook_status = "sent"
                    else:
                        webhook_error = RuntimeError("群机器人接口返回失败")
                        for dv in webhook_pending:
                            dv.webhook_status = "failed"
                        logger.warning("即时群机器人推送返回失败 (user=%s)", user.email)
                except Exception as we:
                    webhook_error = we
                    for dv in webhook_pending:
                        dv.webhook_status = "failed"
                    logger.warning("即时群机器人推送异常: %s", we)
            elif webhook_pending:
                # 订阅要求 Webhook 但用户未配置：标记跳过，避免无限重试
                for dv in webhook_pending:
                    dv.webhook_status = "skipped"

            # 3. 汇总判定：所有已启用通道均成功/跳过才算整条投递成功
            all_channels_ok = True
            for dv in user_deliveries:
                ch = _channel_of(dv)
                if ch in ("both", "email", "all") and (dv.email_status or "pending") not in ("sent", "skipped"):
                    all_channels_ok = False
                if ch in ("both", "webhook", "all") and (dv.webhook_status or "pending") not in ("sent", "skipped"):
                    all_channels_ok = False
            if all_channels_ok:
                for dv in user_deliveries:
                    dv.status = "sent"
                    dv.sent_at = now
                    dv.last_error = None
            else:
                for dv in user_deliveries:
                    mark_delivery_failed(dv, email_error or webhook_error or RuntimeError("部分通道投递失败"), now)

            sent_user_ids.add(user_id)
            if settings.immediate_send_interval_seconds > 0:
                from time import sleep

                sleep(settings.immediate_send_interval_seconds)
        db.commit()
        return

    # 未合并模式（兼容）：逐条发送（同样按双通道子状态处理）
    for delivery in deliveries:
        subs_map = get_user_subscriptions_map(db, delivery.user_id)
        ch = getattr(subs_map.get(delivery.feed_item.xueqiu_account_id), 'delivery_channel', 'both') or 'both'
        should_email = ch in ("both", "email", "all")
        should_webhook = ch in ("both", "webhook", "all")

        email_error: Exception | None = None
        webhook_error: Exception | None = None

        if should_email and (delivery.email_status or "pending") != "sent":
            try:
                inner = render_item(delivery.feed_item)
                body = wrap_email(
                    inner,
                    heading=f"{delivery.feed_item.account.display_name} 有新动态",
                    subheading="实时动态更新",
                    unsubscribe_url=unsubscribe_url(delivery.user_id),
                )
                subject = build_single_delivery_subject(delivery)
                cfg = user_smtp_config(delivery.user)
                send_email(delivery.user.email, subject, body, config=cfg)
                delivery.email_status = "sent"
            except Exception as exc:
                email_error = exc
                delivery.email_status = "failed"
                logger.warning("逐条邮件推送异常: %s", exc)
        elif should_email:
            delivery.email_status = "sent"

        if should_webhook and (delivery.webhook_status or "pending") != "sent":
            if delivery.user.webhook_enabled and delivery.user.webhook_url:
                try:
                    from app.webhook import send_user_webhook, render_item_markdown
                    text = render_item_markdown(delivery.feed_item, include_author=False)
                    alert_hit = bool(getattr(delivery, "is_alert", False))
                    title = f"{delivery.feed_item.account.display_name} 有新动态"
                    if alert_hit:
                        title = f"🚨【强提醒】{title}"
                    if send_user_webhook(delivery.user, title, text, link=delivery.feed_item.link, at_all=alert_hit):
                        delivery.webhook_status = "sent"
                    else:
                        webhook_error = RuntimeError("群机器人接口返回失败")
                        delivery.webhook_status = "failed"
                        logger.warning("逐条群机器人推送返回失败 (user=%s)", delivery.user.email)
                except Exception as we:
                    webhook_error = we
                    delivery.webhook_status = "failed"
                    logger.warning("逐条群机器人推送异常: %s", we)
            else:
                delivery.webhook_status = "skipped"
        elif should_webhook:
            delivery.webhook_status = "sent"

        channels_ok = True
        if should_email and (delivery.email_status or "pending") not in ("sent", "skipped"):
            channels_ok = False
        if should_webhook and (delivery.webhook_status or "pending") not in ("sent", "skipped"):
            channels_ok = False

        if channels_ok:
            delivery.status = "sent"
            delivery.sent_at = now
            delivery.last_error = None
        else:
            mark_delivery_failed(delivery, email_error or webhook_error or RuntimeError("部分通道投递失败"), now)

        db.commit()


def mark_delivery_failed(delivery: Delivery, exc: Exception, now: datetime) -> None:
    delivery.attempts += 1
    delivery.last_error = str(exc)[:2000]
    if delivery.attempts >= settings.delivery_max_attempts:
        delivery.status = "failed"
        # 终态也更新时间基准，供 purge_old_data 按保留期清理
        delivery.next_attempt_at = now
    else:
        delivery.status = "retry"
        delay = settings.retry_base_seconds * (2 ** (delivery.attempts - 1))
        delivery.next_attempt_at = now + timedelta(seconds=delay)


def notify_admin_if_failures(db: Session) -> None:
    """每日一次的运维自检：存在最终失败的投递时，向管理员的群机器人推送告警。
    通过 app_config.last_failure_alert_date 去重，每天最多提醒一次。"""
    from app import appconfig

    today = datetime.now(timezone.utc).date().isoformat()
    if str(appconfig.get_cfg("last_failure_alert_date", "")) == today:
        return
    failed_count = db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "failed"))
    if not failed_count:
        appconfig.set_cfg("last_failure_alert_date", today)
        return
    admins = db.scalars(
        select(User).where(
            User.is_admin.is_(True),
            User.is_enabled.is_(True),
            User.webhook_enabled.is_(True),
            User.webhook_url.is_not(None),
        )
    ).all()
    if not admins:
        # 管理员未配置群机器人则跳过（不打扰，也不置日期，配置后次日生效）
        return
    from app.webhook import send_user_webhook

    logger.warning("检测到 %d 条最终失败的投递，尝试推送运维告警", failed_count)
    for admin in admins:
        try:
            if send_user_webhook(
                admin,
                "🛠 智投内参运维告警",
                f"⚠️ 当前有 **{failed_count} 条投递最终失败**（邮件与群机器人均未送达）。\n\n"
                f"请登录管理后台查看失败详情（last_error）并重试。",
                link=settings.base_url,
            ):
                appconfig.set_cfg("last_failure_alert_date", today)
                logger.info("运维告警已推送给管理员 %s (failed=%d)", admin.email, failed_count)
        except Exception as exc:
            logger.warning("运维告警推送失败 (%s): %s", getattr(admin, "email", "?"), exc)


def alert_missing_digest(db: Session, digest_date) -> None:
    """昨日日报未生成时向管理员的群机器人推送运维告警（按日期去重，每天最多一次）。"""
    from app import appconfig
    from app.webhook import send_user_webhook

    dedup_key = f"last_missing_digest_alert_{digest_date}"
    if str(appconfig.get_cfg(dedup_key, "")):
        return
    admins = db.scalars(
        select(User).where(
            User.is_admin.is_(True),
            User.is_enabled.is_(True),
            User.webhook_enabled.is_(True),
            User.webhook_url.is_not(None),
        )
    ).all()
    for admin in admins:
        try:
            if send_user_webhook(
                admin,
                "🛠 智投内参运维告警",
                f"⚠️ **{digest_date} 的每日汇总未能生成**（AI 总结持续失败或投递异常），昨日复盘已丢失。\n\n请检查 worker 日志与 LLM 接口状态。",
                link=settings.base_url,
            ):
                appconfig.set_cfg(dedup_key, "1")
                logger.warning("已推送日报缺失告警 (%s)", digest_date)
        except Exception as exc:
            logger.warning("日报缺失告警推送失败: %s", exc)


def send_weekly_digests(db: Session, local_now: datetime) -> None:
    """每周日汇总过去 7 天的动态与 AI 周度复盘（全局每周一次，内部去重）。

    周报由 last_weekly_digest_date 全局去重、整周只发一轮，无法按人挑时刻，
    因此仍按全局默认 digest_hour 触发，不读 users.digest_hour。"""
    from app import appconfig
    from app.webhook import send_user_webhook

    today_str = local_now.date().isoformat()
    if str(appconfig.get_cfg("last_weekly_digest_date", "")) == today_str:
        return

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    start_utc_aware = (local_now - timedelta(days=7)).astimezone(timezone.utc)
    start_utc = start_utc_aware.replace(tzinfo=None)
    users = db.scalars(
        select(User).where(User.is_enabled.is_(True), User.email_verified.is_(True))
    ).all()
    logger.info("开始生成周报（%s ~ %s，用户 %d 个）", start_utc.date(), now_utc.date(), len(users))

    for user in users:
        try:
            week_deliveries = db.scalars(
                select(Delivery).join(Delivery.feed_item)
                .options(joinedload(Delivery.feed_item).joinedload(FeedItem.account))
                .where(
                    Delivery.user_id == user.id,
                    Delivery.status == "sent",
                    Delivery.sent_at.is_not(None),
                    Delivery.sent_at >= start_utc,
                    Delivery.sent_at <= now_utc,
                )
                .order_by(FeedItem.published_at.asc())
            ).unique().all()
            if not week_deliveries:
                continue

            # 按动态去重
            items: list[FeedItem] = []
            seen_ids: set[int] = set()
            for d in week_deliveries:
                it = d.feed_item
                if it and it.id not in seen_ids:
                    seen_ids.add(it.id)
                    items.append(it)

            summary = summarize_items_with_llm(items[:80], user=user, db=db, period_label="过去一周")

            subs_map = get_user_subscriptions_map(db, user.id)

            def _weekly_channel_of(dv: Delivery) -> str:
                return getattr(subs_map.get(dv.feed_item.xueqiu_account_id), "delivery_channel", "both") or "both"

            email_deliveries = [d for d in week_deliveries if _weekly_channel_of(d) in ("both", "email", "all")]
            webhook_deliveries = [d for d in week_deliveries if _weekly_channel_of(d) in ("both", "webhook", "all")]
            period = f"{start_utc_aware:%m-%d} ~ {local_now:%m-%d}"

            if email_deliveries:
                try:
                    recent = email_deliveries[-50:]
                    ai_card = render_ai_summary_card(summary) if summary else ""
                    inner = ai_card + "".join(render_item(d.feed_item) for d in recent)
                    email_body = wrap_email(
                        inner,
                        heading=f"本周共 {len(email_deliveries)} 条动态",
                        subheading=f"{period} · 周度复盘",
                        unsubscribe_url=unsubscribe_url(user.id),
                    )
                    subject = f"【智投周报】{period} 大V动态与调仓复盘"
                    send_email(user.email, subject, email_body, config=user_smtp_config(user))
                    logger.info("用户 %s 周报邮件已发送 (%d 条动态)", user.email, len(recent))
                except Exception as exc:
                    logger.warning("用户 %s 周报邮件发送失败: %s", user.email, exc)

            if webhook_deliveries and user.webhook_enabled and user.webhook_url:
                try:
                    if summary:
                        webhook_text = summary
                    else:
                        names = ", ".join(sorted({d.feed_item.account.display_name for d in webhook_deliveries}))
                        webhook_text = f"本周共 {len(webhook_deliveries)} 条动态，来自 {names}。"
                    send_user_webhook(user, f"📊 智投周报 {period}", webhook_text, link=settings.base_url)
                except Exception as exc:
                    logger.warning("用户 %s 周报群机器人推送异常: %s", user.email, exc)
        except Exception as ue:
            db.rollback()
            logger.exception("处理用户 %s 周报异常: %s", getattr(user, "email", "?"), ue)

    # 无论个别用户是否失败都置日期，避免下个周期重复群发；个别失败会记录在日志中
    appconfig.set_cfg("last_weekly_digest_date", today_str)
    logger.info("周报流程完成")








def send_daily_digests(db: Session, local_date, retry_only: bool = False, local_hour: int | None = None) -> None:
    """按用户各自的 digest_hour 发送每日汇总。

    local_hour 传入当前本地整点时，未到该用户发送时刻的直接跳过（不做任何查询）；
    传 None 表示追补/手动触发，忽略小时门控。去重仍依赖 DigestLog(user_id, digest_date)。
    """
    from datetime import date as _date
    from zoneinfo import ZoneInfo
    from app.models import DigestLog, Subscription
    from app import appconfig
    from sqlalchemy import or_, and_

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    target_d = local_date if isinstance(local_date, _date) else (local_date.date() if hasattr(local_date, "date") else local_date)

    # 获取配置的本地时区及起止时间 (UTC 窗口，转为 naive UTC 供 SQLite 查询与内存比对)
    app_tz = ZoneInfo(appconfig.get_cfg("app_timezone", settings.app_timezone))
    start_local = datetime(target_d.year, target_d.month, target_d.day, 0, 0, 0, tzinfo=app_tz)
    end_local = datetime(target_d.year, target_d.month, target_d.day, 23, 59, 59, 999999, tzinfo=app_tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    def _is_pub_after(feed_item, threshold):
        if not feed_item or feed_item.published_at is None:
            return True
        pub = feed_item.published_at
        if getattr(pub, "tzinfo", None) is not None:
            pub = pub.astimezone(timezone.utc).replace(tzinfo=None)
        return pub >= threshold

    # 查询所有有效且通过验证的用户
    users = db.scalars(
        select(User).where(
            User.is_enabled.is_(True),
            User.email_verified.is_(True),
        )
    ).all()

    for user in users:
        user_id = user.id
        try:
            # 每个用户按自己的 digest_hour（为空则回退全局默认）触发；未到点的用户直接跳过，
            # 不产生任何 DigestLog / 投递查询，等同把原先写死在 worker 里的全局门控下沉到人
            if local_hour is not None and local_hour < appconfig.digest_hour_for_user(user):
                continue
            # 检查该用户目标日期是否已经成功生成过夜间汇总
            user_sent_today = db.scalar(
                select(DigestLog.id).where(
                    DigestLog.user_id == user_id,
                    DigestLog.digest_date == target_d,
                )
            ) is not None

            # 1. 检查是否有 daily 模式的待投递任务
            statuses = ("retry", "failed") if (user_sent_today or retry_only) else ("pending", "retry", "failed")
            daily_deliveries = db.scalars(
                select(Delivery).join(Delivery.feed_item).order_by(FeedItem.published_at.is_(None), FeedItem.published_at.asc())
                .options(joinedload(Delivery.feed_item).joinedload(FeedItem.account))
                .where(
                    Delivery.user_id == user_id,
                    Delivery.status.in_(statuses),
                    Delivery.delivery_mode == "daily",
                    Delivery.next_attempt_at <= now,
                )
            ).unique().all()

            if daily_deliveries:
                max_per = int(appconfig.get_cfg("digest_max_per_email", settings.digest_max_per_email))
                if max_per > 0:
                    batches = [daily_deliveries[i:i + max_per] for i in range(0, len(daily_deliveries), max_per)]
                else:
                    batches = [daily_deliveries]
                all_ok = True
                for idx, batch in enumerate(batches):
                    ai_card = ""
                    summary = None
                    if idx == 0:
                        ai_enabled_account_ids = set(
                            db.scalars(
                                select(Subscription.xueqiu_account_id).where(
                                    Subscription.user_id == user_id,
                                    Subscription.is_enabled.is_(True),
                                    Subscription.ai_summary_enabled.is_(True),
                                )
                            ).all()
                        )
                        items_to_summarize = [
                            d.feed_item for d in batch
                            if d.feed_item and d.feed_item.xueqiu_account_id in ai_enabled_account_ids
                            and _is_pub_after(d.feed_item, start_utc - timedelta(days=3))
                        ]
                        if items_to_summarize:
                            summary = summarize_items_with_llm(items_to_summarize, user=user, db=db)
                            if summary:
                                ai_card = render_ai_summary_card(summary)
                    try:
                        subs_map = get_user_subscriptions_map(db, user_id)

                        def _digest_channel_of(dv: Delivery) -> str:
                            return getattr(subs_map.get(dv.feed_item.xueqiu_account_id), "delivery_channel", "both") or "both"

                        # 双通道子状态：已成功的通道本轮不再重复发送
                        email_items = [d for d in batch if _digest_channel_of(d) in ("both", "email", "all") and (d.email_status or "pending") != "sent"]
                        webhook_items = [d for d in batch if _digest_channel_of(d) in ("both", "webhook", "all") and (d.webhook_status or "pending") != "sent"]

                        email_error: Exception | None = None
                        webhook_error: Exception | None = None

                        if email_items:
                            try:
                                cfg = user_smtp_config(user)
                                inner_items = (ai_card if summary else "") + "".join(render_item(d.feed_item) for d in email_items)
                                email_body = wrap_email(
                                    inner_items,
                                    heading=f"{local_date:%Y-%m-%d} 每日汇总",
                                    subheading=f"共 {len(email_items)} 条动态" + (f" · 第 {idx + 1}/{len(batches)} 封" if len(batches) > 1 else ""),
                                    unsubscribe_url=unsubscribe_url(user_id),
                                )
                                email_subject = build_daily_digest_subject(email_items, local_date, idx, len(batches))
                                send_email(user.email, email_subject, email_body, config=cfg)
                                for delivery in email_items:
                                    delivery.email_status = "sent"
                            except Exception as exc:
                                email_error = exc
                                for delivery in email_items:
                                    delivery.email_status = "failed"
                                logger.warning("每日汇总邮件发送失败 (user=%s): %s", user.email, exc)

                        # 群机器人通道：仅第一封附带完整 AI 总结；检查返回值，失败同样进入重试
                        if idx == 0 and webhook_items:
                            if user.webhook_enabled and user.webhook_url:
                                try:
                                    from app.webhook import send_user_webhook
                                    fallback_text = "".join([f"• 【{d.feed_item.account.display_name}】{d.feed_item.title}\n" for d in webhook_items[:10]])
                                    webhook_text = summary if summary else fallback_text
                                    if send_user_webhook(user, f"{local_date:%Y-%m-%d} 每日大V动态与AI投研内参", webhook_text, link=settings.base_url):
                                        for delivery in webhook_items:
                                            delivery.webhook_status = "sent"
                                    else:
                                        webhook_error = RuntimeError("群机器人接口返回失败")
                                        for delivery in webhook_items:
                                            delivery.webhook_status = "failed"
                                        logger.warning("每日汇总群机器人推送返回失败 (user=%s)", user.email)
                                except Exception as we:
                                    webhook_error = we
                                    for delivery in webhook_items:
                                        delivery.webhook_status = "failed"
                                    logger.warning("群机器人推送异常: %s", we)
                            else:
                                # 用户未配置 Webhook：标记跳过，避免无限重试
                                for delivery in webhook_items:
                                    delivery.webhook_status = "skipped"

                        # 送达判定：每个已启用通道都成功/跳过才算整批送达，
                        # 避免给仅用群机器人的用户照写 DigestLog 导致当天汇总永久丢失
                        batch_ok = True
                        for delivery in batch:
                            ch = _digest_channel_of(delivery)
                            if ch in ("both", "email", "all") and (delivery.email_status or "pending") not in ("sent", "skipped"):
                                batch_ok = False
                            if ch in ("both", "webhook", "all") and (delivery.webhook_status or "pending") not in ("sent", "skipped"):
                                batch_ok = False
                        if batch_ok:
                            for delivery in batch:
                                delivery.status = "sent"
                                delivery.sent_at = now
                                delivery.last_error = None
                        else:
                            all_ok = False
                            for delivery in batch:
                                mark_delivery_failed(delivery, email_error or webhook_error or RuntimeError("每日汇总投递失败"), now)
                    except Exception as exc:
                        all_ok = False
                        for delivery in batch:
                            mark_delivery_failed(delivery, exc, now)
                if all_ok and not retry_only:
                    db.add(DigestLog(user_id=user_id, digest_date=_date(local_date.year, local_date.month, local_date.day), sent_at=now))
                db.commit()

            # 2. 途径 B：若该用户未汇总过目标日期，且没有 daily 积压（例如设为了 immediate 实时推送），
            # 汇总当天已通过 immediate 推送过的全量动态，生成一份全天复盘 AI 总结投递给群机器人/邮件
            elif not user_sent_today and not retry_only:
                immediate_today = db.scalars(
                    select(Delivery).join(Delivery.feed_item).order_by(FeedItem.published_at.is_(None), FeedItem.published_at.asc())
                    .options(joinedload(Delivery.feed_item).joinedload(FeedItem.account))
                    .where(
                        Delivery.user_id == user_id,
                        Delivery.status == "sent",
                        Delivery.delivery_mode == "immediate",
                        or_(
                            and_(Delivery.sent_at >= start_utc, Delivery.sent_at <= end_utc),
                            and_(FeedItem.published_at >= start_utc, FeedItem.published_at <= end_utc),
                        )
                    )
                ).unique().all()

                if immediate_today:
                    subs_map = get_user_subscriptions_map(db, user_id)
                    ai_enabled_account_ids = set(
                        db.scalars(
                            select(Subscription.xueqiu_account_id).where(
                                Subscription.user_id == user_id,
                                Subscription.is_enabled.is_(True),
                                Subscription.ai_summary_enabled.is_(True),
                            )
                        ).all()
                    )
                    items_to_summarize = [
                        d.feed_item for d in immediate_today
                        if d.feed_item and d.feed_item.xueqiu_account_id in ai_enabled_account_ids
                        and _is_pub_after(d.feed_item, start_utc - timedelta(days=1))
                    ]
                    # 去重
                    seen_ids = set()
                    unique_summary_items = []
                    for item in items_to_summarize:
                        if item.id not in seen_ids:
                            seen_ids.add(item.id)
                            unique_summary_items.append(item)

                    summary = None
                    if unique_summary_items:
                        summary = summarize_items_with_llm(unique_summary_items, user=user, db=db)

                    webhook_items = [
                        d for d in immediate_today
                        if getattr(subs_map.get(d.feed_item.xueqiu_account_id), "delivery_channel", "both") in ("both", "webhook", "all")
                        and _is_pub_after(d.feed_item, start_utc - timedelta(days=1))
                    ]
                    email_items = [
                        d for d in immediate_today
                        if getattr(subs_map.get(d.feed_item.xueqiu_account_id), "delivery_channel", "both") in ("both", "email", "all")
                        and _is_pub_after(d.feed_item, start_utc - timedelta(days=1))
                    ]

                    # AI 总结失败时本轮不发兜底消息也不写 DigestLog：
                    # 避免“先发兜底列表、重试又发 AI 版”的重复推送，下个周期（10分钟内）自动重试
                    if unique_summary_items and summary is None:
                        logger.warning("用户 %s 夜间 AI 总结生成失败，本轮跳过发送，等待下个周期重试", user.email)
                        db.rollback()
                    else:
                        webhook_ok = True
                        if webhook_items and user.webhook_enabled and user.webhook_url:
                            try:
                                from app.webhook import send_user_webhook
                                fallback_text = "".join([f"• 【{d.feed_item.account.display_name}】{d.feed_item.title}\n" for d in webhook_items[:10]])
                                webhook_text = summary if summary else fallback_text
                                if send_user_webhook(user, f"{target_d:%Y-%m-%d} 每日大V动态与AI投研内参", webhook_text, link=settings.base_url):
                                    logger.info("用户 %s 实时动态夜间 AI 总结已成功推送到群机器人 (共 %d 条动态)", user.email, len(immediate_today))
                                else:
                                    webhook_ok = False
                                    logger.warning("用户 %s 夜间 AI 总结群机器人推送返回失败", user.email)
                            except Exception as we:
                                webhook_ok = False
                                logger.warning("用户 %s 夜间 AI 总结群机器人推送异常: %s", user.email, we)

                        email_ok = True
                        if email_items and summary:
                            try:
                                ai_card = render_ai_summary_card(summary)
                                inner_items = ai_card + "".join(render_item(d.feed_item) for d in email_items)
                                email_body = wrap_email(
                                    inner_items,
                                    heading=f"{target_d:%Y-%m-%d} 每日大V动态与AI投研内参",
                                    subheading=f"全天复盘 · 共 {len(email_items)} 条动态",
                                    unsubscribe_url=unsubscribe_url(user_id),
                                )
                                email_subject = f"【AI投研内参】{target_d:%Y-%m-%d} 每日大V动态与调仓复盘"
                                cfg = user_smtp_config(user)
                                send_email(user.email, email_subject, email_body, config=cfg)
                                logger.info("用户 %s 实时动态夜间 AI 总结已成功发送邮件", user.email)
                            except Exception as me:
                                email_ok = False
                                logger.warning("用户 %s 夜间 AI 总结邮件推送异常: %s", user.email, me)

                        # 任一通道失败则不写 DigestLog，下个周期自动重试
                        if webhook_ok and email_ok:
                            db.add(DigestLog(user_id=user_id, digest_date=_date(target_d.year, target_d.month, target_d.day), sent_at=now))
                            db.commit()
                        else:
                            db.rollback()
        except Exception as ue:
            db.rollback()
            logger.exception("处理用户 %s 每日汇总流程异常: %s", getattr(user, "email", user_id), ue)



