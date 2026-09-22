"""零依赖（仅标准库）的 HTML 安全化助手：邮件渲染前统一处理不可信文本。

为什么需要它
------------
本模块解决两类"不可信文本直接进入 HTML 邮件"的注入：

1. 大模型回复正文（由雪球帖子内容派生，可被提示注入操纵）；
2. RSS 原样入库的帖子 HTML（``services/fetcher.py`` 保存的是 feedparser 的
   ``summary`` 原文，标签剥离正则只用于过滤判定，不改变入库内容）。

设计原则（也是与旧实现的区别）
------------------------------
* **绝不搬运上游标签或属性**。所有输出标签都由本仓库代码字面量写出；
  上游 HTML 只作为"文本 + 少量 href / 图片 src 事实"的来源。
* 解析用标准库 ``html.parser.HTMLParser``（与 bleach 内部同源的思路：词法解析 +
  白名单重建，而非正则补丁）。这不是手写净化器：我们不复用上游字节，
  因此不存在"漏掉某个正则分支"这类失败模式。
* 单一收口：任何写进 ``href`` 或 ``img src`` 的值都必须经过 :func:`safe_href`。

导出的公共 API
--------------
``escape_text``   —— 文本/属性转义（``quote=True``，两处通用）
``safe_href``     —— href 白名单化并返回可直接内联的属性值
``href_host``     —— 取 href 主机名，仅供"站内外链接"展示判断
``html_to_segments`` —— 上游 HTML → 纯文本片段（含链接/图片事实）
``html_to_text``  —— 上游 HTML → 单一纯文本字符串
``one_line``      —— 压成单行（邮件 Subject 用，杜绝 CR/LF/VT 头部注入）

调用侧仍负责排版：片段里的文字要 ``escape_text`` 之后才能进 HTML，
href 要过 :func:`safe_href` 才能进属性。本模块不输出任何 HTML。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape, unescape
from html.parser import HTMLParser
from typing import Iterable, Sequence
from urllib.parse import urljoin, urlsplit

__all__ = [
    "ALLOWED_SCHEMES",
    "SEG_QUOTE",
    "SEG_TEXT",
    "ImageRun",
    "LinkRun",
    "Segment",
    "escape_text",
    "href_host",
    "html_to_segments",
    "html_to_text",
    "one_line",
    "safe_href",
    "segments_text",
]

# 只放行这三个协议：tel/geo/sms 之类虽多数无害，但对本产品（财经摘要）没有价值，
# 保守白名单让 "safe_href 是唯一收口" 这一条更容易被审计。
ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})

SEG_TEXT = "text"
SEG_QUOTE = "quote"

# 整棵子树（含其文本）丢弃：这些元素要么内容根本不是人读正文（CSS/JS 源码、
# 元数据），要么解析器会交出 raw-text，留着就会把脚本源码当正文印进邮件。
# 注意 svg/math **不在**这里：它们在 html.parser 里没有 raw-text 模式，且我们
# 对所有标签一律只丢不搬，保留其后代文本永远是"已转义的 inert 文字"；
# 反过来若整段吞掉，`<svg/onload=x>` 这种永不闭合的写法会把发帖人
# svg 之后的正常正文一起吃掉（实测过的真实回归）。
_DROP_SUBTREE = frozenset({
    "script", "style", "xmp", "plaintext", "listing", "noscript", "noembed",
    "template", "title", "textarea", "iframe", "frame", "frameset", "object",
    "embed", "applet", "xml", "head", "meta", "link", "base",
})


# 这些标签结束（或开始）时视为换行，保留段落观感。
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "br", "hr", "li", "ul", "ol", "tr", "td",
    "th", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "dd", "dt", "dl",
    "figure", "figcaption", "center", "main", "header", "footer", "nav", "aside",
})

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
# C0/C1 控制字符 + 零宽/空白类字符：必须先剥掉再做协议判断，
# 否则 java\tscript: / ​javascript: 会以"无协议"的假象通过。
_STRIPCHAR_RE = re.compile(
    "[\x00-\x20\x7f-\xa0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u3000\ufeff]"
)
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# 转义 / href
# --------------------------------------------------------------------------- #

def escape_text(value: object) -> str:
    """把任意不可信值转成 HTML 文本/属性安全字符串。

    ``quote=True`` 让它同时适用于元素内容与双引号包裹的属性值，
    因此调用点不需要判断"我在哪种上下文"。必须在**任何**美化正则
    （``**bold**``、徽章替换等）之前调用，这样输出里的每个标签都是本仓库写的。
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return escape(value, quote=True)


def _clean_href(raw: object) -> str:
    """剥控制/零宽字符 + 按浏览器语义解一次实体，供协议判定与输出使用。"""
    text = "" if raw is None else str(raw)
    text = _STRIPCHAR_RE.sub("", text)
    # 浏览器在属性里只解一层实体；这里同样只解一层，避免 &amp;#106; 变成 & 之外的惊喜。
    text = unescape(text)
    text = _STRIPCHAR_RE.sub("", text)
    # 浏览器把 URL 里的反斜杠当斜杠（\evil.com ≈ //evil.com），我们也这么归一，
    # 保证"用来判协议的字符串"和"最终写进 href 的字符串"完全一致。
    text = text.replace("\\", "/")
    return text.strip()


def safe_href(url: object, base_url: str = "") -> str:
    """唯一 href 收口：返回可安全内联到 ``href="…"`` 的属性值（已转义）。

    仅 http/https/mailto 通过；其余（javascript:、data:、vbscript:、cid:、
    tel:、相对路径、协议相对 ``//host``……）一律降级为 ``"#"``——链接还在，
    点了没反应，比静默删掉更好排版。

    ``base_url`` 是**可选**的解析基准（帖子自身链接）。给它，协议相对/根相对
    地址才能还原成绝对 http(s) 地址（雪球正文里 ``//xueqiu.com/S/…``、
    ``/H600519`` 很常见）；还原后仍会再判一次协议，所以
    ``urljoin(base, "javascript:…")`` 这种绝对目标照样被拒。
    """
    candidate = _clean_href(url)
    if not candidate:
        return "#"

    if candidate.startswith("//"):
        # 协议相对：有基准就补 https，没有基准一律拒绝（不放行 protocol-relative）
        candidate = ("https:" + candidate) if not base_url else urljoin(base_url, candidate)

    match = _SCHEME_RE.match(candidate)
    if match is None:
        if not base_url:
            return "#"
        candidate = urljoin(_clean_href(base_url), candidate)
        match = _SCHEME_RE.match(candidate)
        if match is None:
            return "#"

    if match.group(1).lower() not in ALLOWED_SCHEMES:
        return "#"
    # 判定用的就是输出用的字符串，杜绝"判 A 发 B"
    return escape_text(candidate)


def href_host(url: object) -> str:
    """返回 href 的小写主机名（无法解析/非白名单协议返回 ``""``）。

    只用于"站内外链接外观区分"这类展示判断，**不作为安全依据**——
    放不放行一律由 :func:`safe_href` 决定。
    """
    candidate = _clean_href(url)
    if _SCHEME_RE.match(candidate) is None:
        return ""
    try:
        return (urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return ""


def one_line(value: object, max_len: int = 0) -> str:
    """压成单行纯文本：CR/LF/VT/FF 及连续空白一律折成一个空格。

    邮件 ``Subject`` 走 compat32 头编码，标题里带 ``\\r\\n`` 就能注入邮件头；
    RSS 标题完全可能带这些字符，所以进 Subject 前必须过一次这里。
    这里**不解实体**（Subject 不是 HTML，``&amp;`` 就该按字面显示）。
    """
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x1f\x7f\u2000-\u200f\u2028\u2029\ufeff\xa0]+", " ", text)
    text = _WS_RE.sub(" ", text).strip()
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


# --------------------------------------------------------------------------- #
# 上游 HTML → 片段
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LinkRun:
    """一个上游 ``<a>``：文本已去标签，``href`` 是**未处理**的原始值。

    渲染方必须用 :func:`safe_href` 包装 :attr:`href`，绝不直接内联。
    """
    text: str
    href: str
    pill: bool = False  # 雪球股票代码胶囊（class="xq_stock"）


@dataclass(frozen=True)
class ImageRun:
    """一段连续的正文配图。只保留 src 这一事实，alt/title/style/宽高一律丢弃。

    ``src`` 是**未处理**的原始值：渲染方必须过 :func:`safe_href` 才能写进
    ``<img src>``，否则等于把净化器的唯一收口旁路掉。
    """
    srcs: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.srcs)


@dataclass(frozen=True)
class Segment:
    kind: str
    runs: tuple[str | LinkRun | ImageRun, ...] = ()
    images: int = 0

    @property
    def text(self) -> str:
        return segments_text(self.runs)


def segments_text(runs: Iterable[str | LinkRun | ImageRun]) -> str:
    """把 runs 拍平成纯文本（链接保文本，图片记为占位词）。"""
    out: list[str] = []
    for run in runs:
        if isinstance(run, str):
            out.append(run)
        elif isinstance(run, LinkRun):
            out.append(run.text)
        else:
            out.append("[图片]" * max(run.count, 1))
    return "".join(out)


class _SegmentBuilder(HTMLParser):
    """把上游 HTML 解析成 (文本 | 链接 | 图片) 运行序列，按 blockquote 切段。

    只"读"不"写"：本类不产生任何 HTML，安全属性由渲染方（转义 + safe_href）保证。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._segments: list[Segment] = []
        self._runs: list[str | LinkRun | ImageRun] = []
        self._kind = SEG_TEXT
        self._drop_depth = 0          # 处于被整体丢弃的子树内
        self._quote_depth = 0
        self._link_stack: list[dict] = []

    # ---- 缓冲工具 --------------------------------------------------------- #

    def _cur(self) -> dict | None:
        return self._link_stack[-1] if self._link_stack else None

    def _sink(self) -> list:
        cur = self._cur()
        return cur["text"] if cur is not None else self._runs

    def _emit_text(self, text: str) -> None:
        if not text or self._drop_depth:
            return
        _append_text(self._sink(), text)

    def _emit_image(self, src: str) -> None:
        if self._drop_depth:
            return
        sink = self._sink()
        if sink and isinstance(sink[-1], ImageRun):
            sink[-1] = ImageRun(sink[-1].srcs + (src,))
        else:
            sink.append(ImageRun((src,)))

    def _break(self) -> None:
        self._emit_text("\n")

    def _close_segment(self) -> None:
        """结束当前片段（把链接里已攒的文本先落回主缓冲）。"""
        while self._link_stack:
            self._pop_link()
        if self._drop_depth:
            return
        runs = _normalize_runs(self._runs)
        if runs:
            self._segments.append(Segment(self._kind, runs, _count_images(runs)))
        self._runs = []

    def _start_segment(self, kind: str) -> None:
        self._kind = kind

    # ---- HTMLParser 回调 -------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = (tag or "").lower()
        if tag in _DROP_SUBTREE:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag == "blockquote":
            if self._quote_depth == 0:
                self._close_segment()
                self._start_segment(SEG_QUOTE)
            self._quote_depth += 1
            return
        if tag == "img":
            self._handle_img(attrs)
            return
        if tag == "a":
            self._push_link(attrs)
            return
        if tag in _BLOCK_TAGS:
            self._break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = (tag or "").lower()
        if tag == "img" and not self._drop_depth:
            self._handle_img(attrs)
        elif tag == "br" and not self._drop_depth:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        tag = (tag or "").lower()
        if tag in _DROP_SUBTREE:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth:
            return
        if tag == "blockquote":
            self._quote_depth = max(0, self._quote_depth - 1)
            if self._quote_depth == 0:
                self._close_segment()
                self._start_segment(SEG_TEXT)
            return
        if tag == "a":
            self._pop_link()
            return
        if tag in _BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if self._drop_depth or not data:
            return
        self._emit_text(data.replace("\xa0", " "))

    # 注释 / CDATA / DOCTYPE / 处理指令：整体丢弃。
    # 顺带解决 `<!--</div><script>…</script>-->`：解析器把中间内容当注释体，我们连注释体都不输出。
    def handle_comment(self, data: str) -> None:  # noqa: D401
        return

    def handle_decl(self, data: str) -> None:
        return

    def unknown_decl(self, data) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def error(self, message: str) -> None:  # pragma: no cover - py<3.10 兼容
        return

    def finish(self) -> tuple[Segment, ...]:
        """收尾：落盘最后一段并返回结果。"""
        self._close_segment()
        return tuple(self._segments)

    # ---- 元素细节 --------------------------------------------------------- #

    def _handle_img(self, attrs: list[tuple[str, str | None]]) -> None:
        got = {k.lower(): (v or "") for k, v in attrs}
        src = got.get("src", "")
        alt = got.get("alt", "").strip()
        title = got.get("title", "").strip()
        # 表情图（[捂脸] 等）用 alt 文本替代；判定条件与旧实现保持一致，避免行为漂移
        is_emoji = (
            "face/" in src
            or "emoji_" in src
            or "imedao.com/ugc/images/face/" in src
            or (len(alt) > 2 and alt.startswith("[") and alt.endswith("]"))
        )
        if is_emoji:
            self._emit_text(alt or title or "[表情]")
        else:
            self._emit_image(src)

    def _push_link(self, attrs: list[tuple[str, str | None]]) -> None:
        got = {k.lower(): (v or "") for k, v in attrs}
        self._link_stack.append({
            "href": got.get("href", ""),
            "pill": "xq_stock" in got.get("class", ""),
            "text": [],
        })

    def _pop_link(self) -> None:
        if not self._link_stack:
            return
        link = self._link_stack.pop()
        text = _normalize_text("".join(link["text"]))
        if not text.strip():
            return
        href = (link["href"] or "").strip()
        if not href:
            # 没有 href 的 <a> 只是排版壳，退化成纯文本
            _append_text(self._sink(), text)
            return
        # 只保留"这条链接存在 + 它的 href 是什么"两个事实，其余属性一律丢弃
        self._sink().append(LinkRun(text, href, pill=link["pill"]))


def _append_text(sink: list, text: str) -> None:
    if sink and isinstance(sink[-1], str):
        sink[-1] += text
    else:
        sink.append(text)


def _normalize_text(text: str) -> str:
    """行尾归一 + 空白折叠：让渲染端只面对 ``\\n`` 一种换行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]{2,}", " ", text)
    text = re.sub(r"[ \t\f\v]*\n[ \t\f\v]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _normalize_runs(runs: Sequence) -> tuple:
    """合并相邻文本块、折叠纯空白 run、合并相邻图片占位，并去掉片段首尾空白。"""
    out: list = []
    for run in runs:
        if isinstance(run, str):
            run = _normalize_text(run)
            if not run.strip():
                # 空白 run：只在两侧都还有内容时留一个分隔符，避免链接文字粘连
                sep = "\n" if "\n" in run else " "
                if not out or not run:
                    continue
                if isinstance(out[-1], str):
                    if not out[-1].endswith(sep):
                        out[-1] += sep if sep == "\n" else " "
                else:
                    out.append(sep)
                continue
            if out and isinstance(out[-1], str):
                out[-1] += run
            else:
                out.append(run)
        elif isinstance(run, ImageRun):
            if out and isinstance(out[-1], ImageRun):
                out[-1] = ImageRun(out[-1].srcs + run.srcs)
            else:
                out.append(run)
        else:
            out.append(run)
    # 去掉首尾空白（含块级标签换来的换行，否则邮件开头会多一个 <br>）
    if out and isinstance(out[0], str):
        out[0] = out[0].lstrip()
        if not out[0]:
            out.pop(0)
    if out and isinstance(out[-1], str):
        out[-1] = out[-1].rstrip()
        if not out[-1]:
            out.pop()
    return tuple(out)


def _count_images(runs: Sequence) -> int:
    return sum(run.count for run in runs if isinstance(run, ImageRun))


def html_to_segments(raw: object) -> tuple[Segment, ...]:
    """上游 HTML → 结构化片段序列（只含文本/链接 href/图片 src 事实）。

    输出不含任何 HTML：转义与标签书写由渲染方负责。
    """
    text = "" if raw is None else str(raw)
    if not text.strip():
        return ()
    builder = _SegmentBuilder()
    builder.feed(text)
    builder.close()
    return builder.finish()


def html_to_text(raw: object) -> str:
    """上游 HTML → 纯文本（段落间空行分隔；链接保文字，图片记为 ``[图片]``）。"""
    parts = [segment.text for segment in html_to_segments(raw)]
    return _normalize_text("\n\n".join(p for p in parts if p.strip())).strip()
