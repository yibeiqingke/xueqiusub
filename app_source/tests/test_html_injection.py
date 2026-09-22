"""不可信文本（雪球帖子 HTML / 大模型回复）进入邮件与群机器人时的注入回归测试。

这些用例的共同断言是 ``assert_inert_html``：输出里出现的每个标签都必须是本仓库
自己写下的字面量，每个 ``href`` 都必须落在 http/https/mailto 白名单内。
"""
import re
from html.parser import HTMLParser

import pytest

from app.htmlsafe import escape_text, html_to_segments, html_to_text, one_line, safe_href
from app.services.digest import render_ai_summary_card
from app.services.notifier import (
    build_single_delivery_subject,
    extract_item_snippet,
    format_email_content,
    render_item,
    wrap_email,
)
from app.webhook import html_to_markdown

# 本仓库邮件模板自己用到的标签/属性集合；上游内容渲染后不允许引入其它标签。
# 判定必须走解析器而不是子串扫描：转义后的文本里出现 "javascript:"、"onerror="
# 是**惰性的显示文字**，只有作为标签属性存在时才是漏洞。
OWN_TAGS = {
    "html", "head", "body", "meta", "title", "table", "thead", "tbody", "tr",
    "td", "th", "div", "span", "a", "br", "strong", "em", "p", "img",
}
OWN_ATTRS = {
    "style", "href", "src", "target", "rel", "width", "height", "cellpadding",
    "cellspacing", "role", "valign", "align", "border", "charset", "name",
    "content", "lang", "size", "color", "bgcolor",
}
SAFE_HREF_RE = re.compile(r"^(?:#|(?:https?:|mailto:)\S*)$", re.I)
SAFE_SRC_RE = re.compile(r"^https?://\S*$", re.I)


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str]] = []
        self.hrefs: list[str] = []
        self.srcs: list[str] = []

    def _collect(self, tag, attrs):
        name = (tag or "").lower()
        self.tags.append(name)
        got = {}
        for key, value in attrs or []:
            key = (key or "").lower()
            self.attrs.append((name, key))
            got[key] = value or ""
        if "href" in got:
            self.hrefs.append(got["href"])
        if "src" in got:
            self.srcs.append(got["src"])

    def handle_starttag(self, tag, attrs):
        self._collect(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._collect(tag, attrs)


def assert_inert_html(html: str) -> str:
    """断言 ``html`` 不可执行：标签与属性都只能是本仓库写下的字面量，
    且 href/src 落在协议白名单内（浏览器真正会去请求的那个值，实体已还原）。"""
    collector = _TagCollector()
    collector.feed(html)
    foreign = sorted(set(collector.tags) - OWN_TAGS)
    assert not foreign, f"输出了非本仓库字面量的标签：{foreign}"
    bad_attrs = sorted({a for _t, a in collector.attrs if a not in OWN_ATTRS})
    assert not bad_attrs, f"输出了上游属性（净化器漏收口）：{bad_attrs}"
    for value in collector.hrefs:
        assert SAFE_HREF_RE.match((value or "").strip()), f"href 越界：{value!r}"
    for value in collector.srcs:
        assert SAFE_SRC_RE.match((value or "").strip()), f"img src 越界：{value!r}"
    return html


# --------------------------------------------------------------------------- #
# safe_href / escape_text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("payload", [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    " javascript:alert(1)",
    "​javascript:alert(1)",
    "&#106;avascript:alert(1)",
    "&amp;#106;avascript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "data:image/svg+xml,<svg/>",
    "tel:+123456",
    "cid:abc@def",
    "#javascript:alert(1)",
    "",
    None,
    "not a url at all",
])
def test_safe_href_rejects_or_degrades(payload):
    assert safe_href(payload) == "#"


def test_safe_href_keeps_useful_links():
    assert safe_href("https://xueqiu.com/123/456").startswith("https://xueqiu.com/123/456")
    assert safe_href("http://a.example.com/x").startswith("http://")
    assert safe_href("mailto:a@b.com").startswith("mailto:")


def test_safe_href_resolves_relative_with_base():
    # 雪球正文里常见的协议相对/根相对地址：有基准要还原成可点链接
    assert safe_href("//xueqiu.com/S/600519", base_url="https://xueqiu.com/1/2") == "https://xueqiu.com/S/600519"
    assert safe_href("/S/600519", base_url="https://xueqiu.com/1/2") == "https://xueqiu.com/S/600519"
    # 还原之后仍会重判协议
    assert safe_href("javascript:alert(1)", base_url="https://xueqiu.com/1/2") == "#"


def test_safe_href_escapes_attribute_context():
    out = safe_href('https://xueqiu.com/1/2?q=<img src=x onerror="a">&z=1')
    # 属性上下文：尖括号与引号必须已成实体，无法提前闭合 href
    assert "<" not in out and ">" not in out and '"' not in out
    assert out.startswith("https://xueqiu.com/1/2?q=&lt;img")
    # 附带代价：URL 里的裸空格会被剥掉（真实链接用 %20，不含裸空格）
    assert safe_href("https://xueqiu.com/info?dt=2026-09-19&g=1") == \
        "https://xueqiu.com/info?dt=2026-09-19&amp;g=1"


def test_escape_text_quotes_and_angles():
    assert escape_text('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;"
    assert escape_text(None) == ""


def test_one_line_kills_header_injection():
    assert one_line("标题\r\nBcc: evil@example.com") == "标题 Bcc: evil@example.com"
    assert "\r" not in one_line("a\x0b\nc") and "\n" not in one_line("a\x0b\nc")


# --------------------------------------------------------------------------- #
# 帖子 HTML → 邮件正文
# --------------------------------------------------------------------------- #

HOSTILE_BODY = """
<p>半导体扩产 <img src="https://x.com/a.jpg" alt="[捂脸]"> </p>
<a class="xq_stock" href="https://xueqiu.com/S/SH600519">$贵州茅台(SH600519)$</a>
<a href="javascript:alert(1)">点我有奖</a>
<a href="https://evil.example/<img src=x onerror=alert(1)>">外链</a>
<img src="x" onerror="alert(document.cookie)">
<img src="data:image/svg+xml,%3Csvg onload=alert(1)%3E" alt="配图">
<img src="https://img.xq.com/pic1.jpg!custom.jpg">
<img src="https://img.xq.com/pic1.jpg">
<img src="https://img.xq.com/pic2.jpg">
<scri<script>pt>alert(1)</script>
<script>fetch('//evil')</script>
<iframe src="https://evil"></iframe>
<!-- <img src=real.jpg> --><svg/onload=alert(1)>
<blockquote>被转发帖 <a href="/S/000001">平安银行</a> 正文<br>第二行
<img src="https://img.xq.com/quoted.png"></blockquote>
<style>body{background:red}</style>
"""


def test_hostile_post_body_is_inert():
    out = assert_inert_html(format_email_content(HOSTILE_BODY, base_url="https://xueqiu.com/1/2"))
    # 有价值的站内链接仍要保住
    assert "SH600519" in out
    assert "href=\"https://xueqiu.com/S/SH600519\"" in out
    # 表情还原为文字
    assert "[捂脸]" in out
    # 恶意链接退化为不可点，但文字保留
    assert "点我有奖" in out
    # 引用块保留
    assert "被转发帖" in out
    # script/style/注释体一律不进正文
    assert "fetch(" not in out and "background:red" not in out and "real.jpg" not in out
    # 上游文字即使含 "<script"，也只能以实体形式出现在文本节点里
    assert "<script" not in out.lower()


def test_gallery_images_are_inlined_but_scheme_checked():
    out = format_email_content(HOSTILE_BODY, base_url="https://xueqiu.com/1/2")
    srcs = re.findall(r'<img src="([^"]+)"', out)
    # 只有 http(s) 出图：data: 图与注释里的图都被丢掉；相对 src 按帖子地址还原；
    # !custom.jpg 缩略图在进 safe_href 之前就换成了原图地址，因此 pic1 只出现一次
    assert srcs == ["https://xueqiu.com/1/x", "https://img.xq.com/pic1.jpg",
                    "https://img.xq.com/pic2.jpg", "https://img.xq.com/quoted.png"]
    assert "!custom.jpg" not in out
    # 引用帖的图单独成组，不混进主帖相册
    quote_part = out[out.index("被转发帖"):]
    assert 'src="https://img.xq.com/quoted.png"' in quote_part
    assert "pic2.jpg" not in quote_part
    # 相册外层链接指向原图本身，且带 noopener
    assert 'rel="noopener noreferrer nofollow"' in out
    # 图片被抽进相册后，原先围着它的连续 <br> 要折叠掉（旧实现同样如此）
    assert "<br><br><br>" not in out


def test_gallery_drops_unsafe_and_empty_sources():
    out = format_email_content('<p>正文</p><img src="javascript:alert(1)"><img src="data:image/png;base64,AA"><img src="">')
    assert "<img" not in out
    assert_inert_html(out)


def test_nested_tag_bypass_does_not_reconstitute_script():
    # <scri<script>ipt> 这类"内层闭合后外层残留"的写法是旧正则实现的主要绕过面
    out = format_email_content("<p>a</p><scri<script>pt>alert(1)</script><p>b</p>")
    assert "<script" not in out.lower()
    assert_inert_html(out)


def test_unclosed_script_fails_closed():
    out = format_email_content("<p>before</p><script>never closes")
    assert "never closes" not in out


def test_html_to_text_flattens():
    assert html_to_text("<p>甲</p><p>乙<br>丙</p>") == "甲\n\n乙\n丙"
    assert html_to_text("<a href='https://x/1'>链接文字</a>") == "链接文字"


def test_plain_text_rendering_escapes():
    from app.services.notifier import plain_text_to_html

    out = assert_inert_html(plain_text_to_html('第一行\n第二行 <img src=x onerror=alert(1)>\n\n\n\n第四行'))
    assert "<br>" in out and "<img" not in out


# --------------------------------------------------------------------------- #
# 大模型回复 → 日报卡片
# --------------------------------------------------------------------------- #

def test_ai_summary_card_escapes_model_output():
    md = (
        "## 情绪晴雨表 <img src=x onerror=alert(1)>\n"
        "- **调仓** <script>alert(2)</script>\n"
        "- 【买入】$茅台$ <a href=\"javascript:alert(3)\">链接</a>\n"
        "<scri<script>pt>alert(4)</script>\n"
        "普通段落 <iframe src=//evil>\n"
    )
    out = assert_inert_html(render_ai_summary_card(md))
    assert "情绪晴雨表" in out
    assert re.search(r"<strong[ >]", out)  # 加粗美化仍工作
    assert "买入" in out              # 徽章仍工作
    assert "<a " not in out and "<img" not in out and "<script" not in out.lower()


# --------------------------------------------------------------------------- #
# 整封邮件 / 主题头
# --------------------------------------------------------------------------- #

class _FakeAccount:
    display_name = '大V"><script>alert(9)</script>'


class _FakeItem:
    title = '标题<img src=x onerror=alert(8)>'
    link = "https://xueqiu.com/123/456"
    content = HOSTILE_BODY
    published_at = None
    account = _FakeAccount()
    xueqiu_account_id = 1


class _FakeDelivery:
    feed_item = _FakeItem()
    is_alert = False


def test_render_item_is_inert():
    assert_inert_html(render_item(_FakeItem()))


def test_wrap_email_escapes_author_derived_heading():
    body = wrap_email(
        render_item(_FakeItem()),
        heading='博主 <script>alert(7)</script> 有新动态',
        subheading="来自 evil&co",
        unsubscribe_url="https://app.example/unsubscribe/abc",
    )
    assert_inert_html(body)
    assert "&lt;script&gt;" in body
    assert "evil&amp;co" in body
    assert "<script>alert(7)" not in body


def test_wrap_email_degrades_bad_base_url():
    body = wrap_email("<div>x</div>", "h", "s", "javascript:alert(1)")
    assert_inert_html(body)


def test_subjects_are_single_line():
    class _NastyItem:
        title = "正常标题\r\nBcc: evil@example.com"
        content = "正文"
        link = "https://x/1"
        published_at = None
        account = _FakeAccount()
        xueqiu_account_id = 1

    snippet = extract_item_snippet(_NastyItem())
    assert "\r" not in snippet and "\n" not in snippet
    subject = build_single_delivery_subject(_FakeDelivery())
    # Subject 是邮件头：真正的注入面是 CR/LF 造出第二个头，而不是尖括号
    assert "\r" not in subject and "\n" not in subject
    assert "\r\n" not in f"Subject: {subject}"


# --------------------------------------------------------------------------- #
# 群机器人 Markdown
# --------------------------------------------------------------------------- #

def test_webhook_markdown_links_are_scheme_checked():
    md = html_to_markdown(HOSTILE_BODY)
    assert "javascript:" not in md
    assert "](data:" not in md
    # 正常链接保留
    assert "https://xueqiu.com/S/SH600519" in md


def test_html_to_segments_exposes_only_text_and_href_facts():
    segments = html_to_segments('<div data-x="1" onclick="evil()">正文 <a href="/S/1" title="t">标题</a></div>')
    runs = [run for seg in segments for run in seg.runs]
    assert any(getattr(run, "href", None) == "/S/1" for run in runs)
    assert "onclick" not in repr(runs) and "evil()" not in repr(runs)


# --------------------------------------------------------------------------- #
# 敏感凭据只回显哨兵（第 4 项）
#
# 这里只测**无需数据库**的纯函数：GET/POST 的完整轮转语义（哨兵不改写、空串清除、
# 新值加密落盘、digest_hour 按用户回落全局）由部署闸门 gate_group2.py 在一次性
# 临时库上跑真实 handler 验证——本仓库的测试套件不写任何库文件。
# --------------------------------------------------------------------------- #

def test_mask_secret_only_reports_configured_state():
    from app.main import SECRET_MASK, mask_secret

    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("enc:ciphertext") == SECRET_MASK
    # 掩码是固定长度，绝不透露尾号或长度
    assert mask_secret("a") == mask_secret("very-long-ciphertext-value") == SECRET_MASK


def test_secret_sentinel_roundtrip_semantics():
    from app.main import SECRET_MASK, is_new_secret

    # 回显的哨兵原样提交 => 不改写；空串 => 清除；其余非空 => 写入新值
    assert is_new_secret(SECRET_MASK) is False
    assert is_new_secret("") is False
    assert is_new_secret("   ") is False
    assert is_new_secret("sk-new-key") is True
