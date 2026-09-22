"""多渠道群机器人 Webhook 推送模块（飞书 / 钉钉 / 企业微信）。"""
import base64
import hashlib
import hmac
import logging
import re
import time
from html import unescape
import httpx

from app.htmlsafe import safe_href
from app.security import decrypt_secret
from app.config import get_settings

logger = logging.getLogger("app.webhook")


def md_href(url: str) -> str:
    """把上游 URL 变成可安全写进 Markdown ``[文字](URL)`` 的目标。

    协议白名单沿用 :func:`app.htmlsafe.safe_href`（非 http/https/mailto 会被降级为
    ``"#"``，这里直接视为不可点）；Markdown 不在 HTML 属性上下文里，所以取回未转义
    形态，另外剥掉能提前闭合链接语法或换行的字符。
    """
    escaped = safe_href(url)
    if escaped == "#":
        return ""
    candidate = unescape(escaped)
    return re.sub(r"[\s()\[\]<>`*]", "", candidate)


def html_to_markdown(raw_html: str) -> str:
    """将 HTML 正文转换为适合钉钉/飞书/企微 Markdown 渲染的文本，支持图片、表情、引用与链接。"""
    if not raw_html:
        return ""
    
    text = raw_html
    
    # 1. 表情包图片转换为纯文本 [表情]
    def replace_emoji(m):
        attrs = m.group(1)
        alt_m = re.search(r'alt=[\'"]([^\'"]+)[\'"]', attrs, re.I)
        title_m = re.search(r'title=[\'"]([^\'"]+)[\'"]', attrs, re.I)
        src_m = re.search(r'src=[\'"]([^\'"]+)[\'"]', attrs, re.I)
        src = src_m.group(1) if src_m else ""
        alt = alt_m.group(1) if alt_m else ""
        title = title_m.group(1) if title_m else ""
        if "face/" in src or "emoji_" in src or (alt.startswith("[") and alt.endswith("]")):
            # 无 alt 时保留占位文本，避免表情直接消失
            return alt or title or "[表情]"
        return m.group(0)

    text = re.sub(r'<img\s+([^>]*?)>', replace_emoji, text, flags=re.I)

    # 2. 提取正文大图并去重
    images = []
    seen_urls = set()
    def process_image(m):
        attrs = m.group(1)
        src_m = re.search(r'src=[\'"]([^\'"]+)[\'"]', attrs, re.I)
        if not src_m:
            return ""
        raw_url = src_m.group(1).strip()
        if "face/" in raw_url or "emoji_" in raw_url:
            return ""
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        clean_url = re.sub(r'!(?:custom|800|thumb|\d+)\.jpg$', '', raw_url)
        if clean_url not in seen_urls:
            seen_urls.add(clean_url)
            images.append(clean_url)
        return ""

    text = re.sub(r'<img\s+([^>]*?)>', process_image, text, flags=re.I)

    # 3. 处理 blockquote (转换为 Markdown 引用语法 > )
    def process_blockquote(m):
        inner = m.group(1)
        inner = re.sub(r'<br\s*/?>', '\n', inner, flags=re.I)
        inner = re.sub(r'<[^>]+>', '', inner)
        inner = unescape(inner).strip()
        quote_lines = [f"> {line}" for line in inner.split('\n') if line.strip()]
        return "\n\n" + "\n".join(quote_lines) + "\n\n"

    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', process_blockquote, text, flags=re.I | re.S)

    # 4. 处理超链接 <a href="...">...</a>
    def process_link(m):
        url = m.group(1)
        label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not label:
            return ""
        href = md_href(url)
        return f"[{label}]({href})" if href else label

    text = re.sub(r'<a\s+[^>]*href=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</a>', process_link, text, flags=re.I | re.S)

    # 5. 处理换行与空白
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'</p>', '\n\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    
    # 清理多余连续换行
    lines = [line.strip() for line in text.split('\n')]
    cleaned_lines = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                cleaned_lines.append("")
                prev_empty = True
        else:
            cleaned_lines.append(line)
            prev_empty = False
    
    main_text = "\n".join(cleaned_lines).strip()

    # 6. 追加 Markdown 格式图片
    if images:
        safe_images = [u for u in (md_href(x) for x in images) if u]
        if safe_images:
            img_md = "\n\n".join(f"![图片]({u})" for u in safe_images)
            main_text = f"{main_text}\n\n{img_md}" if main_text else img_md

    return main_text


def render_item_markdown(item, include_author: bool = True) -> str:
    """将单条 FeedItem 格式化为 Markdown 卡片文本。"""
    from datetime import timezone
    author = item.account.display_name if item.account else "雪球用户"
    pub_time = ""
    if item.published_at:
        dt = item.published_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        import zoneinfo
        try:
            loc_dt = dt.astimezone(zoneinfo.ZoneInfo(get_settings().app_timezone))
            pub_time = loc_dt.strftime("%m-%d %H:%M")
        except Exception:
            pub_time = dt.strftime("%m-%d %H:%M")

    body_md = html_to_markdown(item.content or item.title or "")
    
    parts = []
    if include_author:
        header = f"**👤 {author}**"
        if pub_time:
            header += f" ({pub_time})"
        parts.append(header)
    
    if body_md:
        parts.append(body_md)

    # 每条动态自带原文链接：合并推送中每条都可独立直达原帖
    if item.link and md_href(item.link):
        parts.append(f"[👉 查看原文]({md_href(item.link)})")

    return "\n\n".join(parts)


def send_user_webhook(user, title: str, text: str, link: str | None = None, at_all: bool = False) -> bool:
    """根据用户配置的 Webhook 发送群机器人通知。at_all=True 时飞书消息 @所有人，
    其他平台仅通过标题 🚨 前缀提示（钉钉/企微 markdown 不支持程序化 @all）。"""
    if not user or not user.webhook_enabled or not user.webhook_url:
        return False

    settings = get_settings()
    raw_url = decrypt_secret(user.webhook_url, settings.secret_key).strip()
    if not raw_url:
        return False

    secret = decrypt_secret((user.webhook_secret or "")).strip()
    w_type = (user.webhook_type or "").strip().lower()

    # 自动识别类型
    if not w_type or w_type == "auto":
        if "feishu.cn" in raw_url or "larksuite.com" in raw_url:
            w_type = "feishu"
        elif "dingtalk.com" in raw_url:
            w_type = "dingtalk"
        elif "weixin.qq.com" in raw_url or "qyapi.weixin" in raw_url:
            w_type = "wecom"
        else:
            w_type = "feishu"

    try:
        if w_type == "feishu":
            return _send_feishu(raw_url, title, text, link, secret, at_all=at_all)
        elif w_type == "dingtalk":
            return _send_dingtalk(raw_url, title, text, link, secret)
        elif w_type == "wecom":
            return _send_wecom(raw_url, title, text, link)
        else:
            logger.warning("未知的 Webhook 类型: %s", w_type)
            return False
    except Exception as exc:
        logger.warning("Webhook 推送失败 (%s): %s", w_type, exc)
        return False



def format_for_feishu_card(raw_md: str) -> str:
    """将标准 Markdown 转换为飞书卡片 lark_md 完美支持的富文本语法。
    飞书 lark_md 不支持 #、##、### 标题和 Markdown 表格语法，需做平滑视觉映射。"""
    if not raw_md:
        return ""
    
    text = raw_md
    
    # 1. 转换 Markdown 表格为结构清晰的列表
    lines = text.split("\n")
    new_lines = []
    in_table = False
    headers = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c).issubset({"-", ":", " "}) for c in cells):
                in_table = True
                continue
            if not in_table:
                headers = cells
                in_table = True
                continue
            else:
                if headers and len(cells) == len(headers):
                    row_desc = " · ".join(f"**{h}**: {c}" for h, c in zip(headers, cells) if c)
                    new_lines.append(f"• {row_desc}")
                else:
                    new_lines.append(f"• " + " · ".join(cells))
                continue
        else:
            in_table = False
            headers = []

        new_lines.append(line)
        
    text = "\n".join(new_lines)

    # 2. 转换各类标题为飞书高亮视觉标题 (lark_md 不支持 #)
    text = re.sub(r"^# (.+)$", r"\n📌 **\1**", text, flags=re.M)
    text = re.sub(r"^## (.+)$", r"\n🔹 **\1**", text, flags=re.M)
    text = re.sub(r"^### (.+)$", r"\n▫️ **\1**", text, flags=re.M)
    text = re.sub(r"^#### (.+)$", r"\n▪️ **\1**", text, flags=re.M)

    # 3. 转换分割线 --- 为优雅的分割线
    text = re.sub(r"^---+$", "────────────────────────", text, flags=re.M)

    # 4. 优化买卖标签颜色 (Feishu lark_md 支持 <font color='green|red'>)
    text = text.replace("【买入】", "<font color='red'>**【买入】**</font>")
    text = text.replace("【加仓】", "<font color='red'>**【加仓】**</font>")
    text = text.replace("【建仓】", "<font color='red'>**【建仓】**</font>")
    text = text.replace("【减仓】", "<font color='green'>**【减仓】**</font>")
    text = text.replace("【止盈】", "<font color='green'>**【止盈】**</font>")
    text = text.replace("【清仓】", "<font color='green'>**【清仓】**</font>")
    text = text.replace("【观望】", "<font color='grey'>**【观望】**</font>")

    # 5. 去除多余空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _get_feishu_app_credentials() -> tuple[str, str] | None:
    """管理员在后台配置的飞书自建应用凭证（用于上传图片换取 image_key）。"""
    from app import appconfig
    from app.security import decrypt_secret

    app_id = str(appconfig.get_cfg("feishu_app_id", "")).strip()
    secret = decrypt_secret(str(appconfig.get_cfg("feishu_app_secret", ""))).strip()
    if app_id and secret:
        return app_id, secret
    return None


_tenant_token_cache: dict[str, tuple[str, float]] = {}


def _get_tenant_access_token(app_id: str, app_secret: str) -> str | None:
    import time as _time

    cached = _tenant_token_cache.get(app_id)
    if cached and cached[1] > _time.time() + 60:
        return cached[0]
    try:
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("获取飞书 tenant_access_token 失败: %s", data)
            return None
        token = data["tenant_access_token"]
        expire = int(data.get("expire", 7200))
        _tenant_token_cache[app_id] = (token, _time.time() + max(300, expire - 300))
        return token
    except Exception as exc:
        logger.warning("获取飞书 tenant_access_token 异常: %s", exc)
        return None


def upload_feishu_image(app_id: str, app_secret: str, image_url: str) -> str | None:
    """下载外链图片并上传到飞书，返回 image_key；失败返回 None。"""
    try:
        token = _get_tenant_access_token(app_id, app_secret)
        if not token:
            return None
        img_resp = httpx.get(image_url, timeout=20.0, follow_redirects=True)
        if img_resp.status_code != 200 or len(img_resp.content) < 500:
            logger.warning("下载图片失败 (%s): HTTP %s", image_url[:80], img_resp.status_code)
            return None
        content_type = (img_resp.headers.get("content-type") or "image/jpeg").split(";")[0]
        ext = "png" if "png" in content_type else ("gif" if "gif" in content_type else "jpeg")
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (f"image.{ext}", img_resp.content, content_type)},
            timeout=20.0,
        )
        data = resp.json()
        if data.get("code") == 0 and data.get("data", {}).get("image_key"):
            return data["data"]["image_key"]
        logger.warning("飞书图片上传失败: %s", data)
        return None
    except Exception as exc:
        logger.warning("飞书图片上传异常 (%s): %s", image_url[:80], exc)
        return None


def _send_feishu(url: str, title: str, text: str, link: str | None, secret: str | None, at_all: bool = False) -> bool:
    timestamp = str(int(time.time()))
    formatted_text = format_for_feishu_card(text)
    if at_all and formatted_text:
        # 飞书 markdown 组件支持 <at id=all></at> 实现真正的 @所有人
        formatted_text = "<at id=all></at>\n\n" + formatted_text
    # markdown 组件不支持 <font> 颜色标签，回退为纯粗体（买卖标签文字保留）
    formatted_text = re.sub(r"<font\s+color=[\'\"][^\'\"]*[\'\"]>(.*?)</font>", r"\1", formatted_text, flags=re.I)

    # 尝试上传外链图片换取 image_key（需管理员配置飞书应用凭证；未配置或失败则降级为图片链接）
    creds = _get_feishu_app_credentials()
    img_keys: dict[str, str] = {}
    if creds:
        img_urls = []
        for m in re.finditer(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", formatted_text):
            u = m.group(1).strip()
            if u not in img_keys:
                img_urls.append(u)
        for u in img_urls[:9]:
            key = upload_feishu_image(creds[0], creds[1], u)
            if key:
                img_keys[u] = key
    _img_counter = [0]

    def _replace_img(m):
        u = m.group(2).strip()
        if u in img_keys:
            # 用 image_key 占位，构建元素时转为真正的 <img> 图片元素
            return f"\u2063IMG:{img_keys[u]}\u2063"
        _img_counter[0] += 1
        return f"[🖼 图片{_img_counter[0]}]({u})"

    formatted_text = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", _replace_img, formatted_text)

    # 智能分块处理长文，避免单元素超限截断
    chunks = []
    curr = []
    curr_len = 0
    for para in formatted_text.split("\n\n"):
        if curr_len + len(para) > 2800 and curr:
            chunks.append("\n\n".join(curr))
            curr = [para]
            curr_len = len(para)
        else:
            curr.append(para)
            curr_len += len(para)
    if curr:
        chunks.append("\n\n".join(curr))

    elements = []
    top_chunks = chunks[:15]
    for idx, chunk in enumerate(top_chunks):
        # 段内可能含图片占位符：拆分为 markdown 文本与 <img> 图片元素交替
        parts = re.split(r"(\u2063IMG:[^\u2063]+\u2063)", chunk)
        buf = ""
        for part in parts:
            m_img = re.match(r"\u2063IMG:([^\u2063]+)\u2063", part)
            if m_img:
                if buf.strip():
                    elements.append({"tag": "markdown", "content": buf.strip()})
                    buf = ""
                elements.append({
                    "tag": "img",
                    "img_key": m_img.group(1),
                    "alt": {"tag": "plain_text", "content": "图片"},
                })
            else:
                buf += part
        if buf.strip():
            elements.append({"tag": "markdown", "content": buf.strip()})
        if idx < len(top_chunks) - 1:
            elements.append({"tag": "hr"})

    if link:
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔗 查看详情 / 原文"},
                    "type": "primary",
                    "url": link,
                }
            ],
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📈 {title}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }

    if secret:
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    resp = httpx.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0 and data.get("StatusCode") != 0:
        logger.warning("飞书推送返回错误: %s", data)
    return data.get("code") == 0 or data.get("StatusCode") == 0


def _send_dingtalk(url: str, title: str, text: str, link: str | None, secret: str | None) -> bool:
    target_url = url
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")
        import urllib.parse
        target_url = f"{url}&timestamp={timestamp}&sign={urllib.parse.quote_plus(sign)}"

    md_content = f"### {title}\n\n{text}"
    if link:
        md_content += f"\n\n[👉 查看原文]({link})"

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title[:30],
            "text": md_content[:3500],
        },
    }
    resp = httpx.post(target_url, json=payload, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") != 0:
        logger.warning("钉钉推送返回错误: %s", data)
    return data.get("errcode") == 0



def format_for_wecom_md(raw_md: str) -> str:
    """将标准 Markdown 转换为企业微信群机器人支持的语法规范。
    企业微信不支持 #、## (仅支持 ###~######)，不支持表格，颜色仅支持 info, warning, comment。"""
    if not raw_md:
        return ""
    text = raw_md
    
    # 1. 表格转列表
    lines = text.split("\n")
    new_lines = []
    in_table = False
    headers = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c).issubset({"-", ":", " "}) for c in cells):
                in_table = True
                continue
            if not in_table:
                headers = cells
                in_table = True
                continue
            else:
                if headers and len(cells) == len(headers):
                    row_desc = " · ".join(f"**{h}**: {c}" for h, c in zip(headers, cells) if c)
                    new_lines.append(f"• {row_desc}")
                else:
                    new_lines.append(f"• " + " · ".join(cells))
                continue
        else:
            in_table = False
            headers = []
        new_lines.append(line)
    text = "\n".join(new_lines)

    # 2. 转换一级与二级标题为 ### 兼容语法
    text = re.sub(r"^# (.+)$", r"### <font color=\"info\">\1</font>", text, flags=re.M)
    text = re.sub(r"^## (.+)$", r"### \1", text, flags=re.M)
    text = re.sub(r"^---+$", "──────────────", text, flags=re.M)

    # 3. 企微颜色映射
    text = text.replace("【买入】", '<font color="warning">【买入】</font>')
    text = text.replace("【加仓】", '<font color="warning">【加仓】</font>')
    text = text.replace("【建仓】", '<font color="warning">【建仓】</font>')
    text = text.replace("【减仓】", '<font color="info">【减仓】</font>')
    text = text.replace("【止盈】", '<font color="info">【止盈】</font>')
    text = text.replace("【清仓】", '<font color="comment">【清仓】</font>')
    text = text.replace("【观望】", '<font color="comment">【观望】</font>')

    return text


def _send_wecom(url: str, title: str, text: str, link: str | None) -> bool:
    formatted_text = format_for_wecom_md(text)
    md_content = f"### <font color=\"info\">{title}</font>\n\n{formatted_text}"
    if link:
        md_content += f"\n\n[👉 查看原文]({link})"

    # 严格按 UTF-8 字节切片，防止超过企微 4096 字节硬限制报错
    encoded = md_content.encode("utf-8")
    if len(encoded) > 3800:
        encoded = encoded[:3800]
        md_content = encoded.decode("utf-8", errors="ignore") + "\n\n*(内容较长已自动精简，完整请查看详情)*"

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": md_content,
        },
    }
    resp = httpx.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") != 0:
        logger.warning("企业微信推送返回错误: %s", data)
    return data.get("errcode") == 0