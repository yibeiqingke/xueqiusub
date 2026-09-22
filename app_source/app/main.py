import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db, run_migrations
from app.htmlsafe import escape_text, safe_href
from app.mailer import send_email
from app.models import Delivery, FeedItem, Subscription, User, XueqiuAccount
from app.security import encrypt_secret, hash_password, make_token, new_csrf_token, read_token, verify_password


logger = logging.getLogger(__name__)
settings = get_settings()
base_dir = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=base_dir / "templates")

# ============================================================
# 敏感凭据（API Key / 授权码 / Webhook 链接与加签密钥）掩码约定
#   GET ：已配置 -> 固定返回 SECRET_MASK；未配置 -> 返回 ""，并附 has_xxx 标记。
#         后端全程不解密，明文绝不进入 JSON 响应，SPA 被 XSS 也只能拿到哨兵。
#   POST：提交值 == SECRET_MASK -> 保持原值不变；非空且非哨兵 -> 写入新值；
#         空串 "" -> 删除已存凭据（用户清除凭据的唯一途径，语义必须保留）。
#   不提供「末 4 位」之类的提示：哨兵要与 POST 提交值逐字节相等才能判定为
#   「未改动」，掺入逐用户可变内容会破坏该比较；且企微/飞书 webhook 的凭据本身
#   就是 URL 尾部那段不算长的 key，短值时末 4 位占熵比例过高，一律不给提示。
# ============================================================
SECRET_MASK = "********"


def mask_secret(stored: str | None) -> str:
    """敏感字段的回显值：已配置返回固定掩码，未配置返回空串（避免前端回填哨兵造出幽灵凭据）。"""
    return SECRET_MASK if stored else ""


def is_new_secret(submitted: str) -> bool:
    """提交值是否需要写入：非空且不等于掩码哨兵；纯空白按「未改动」处理。"""
    value = submitted.strip()
    return bool(value) and value != SECRET_MASK


def format_local_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    from app import appconfig

    tz = appconfig.get_cfg("app_timezone", settings.app_timezone)
    return value.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


templates.env.filters["local_datetime"] = format_local_datetime


def bootstrap_admin() -> None:
    if not settings.admin_email or not settings.admin_password:
        return
    with SessionLocal() as db:
        email = settings.admin_email.strip().lower()
        if db.scalar(select(User.id).where(User.email == email)):
            return
        db.add(User(
            email=email,
            password_hash=hash_password(settings.admin_password),
            email_verified=True,
            is_admin=True,
        ))
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    run_migrations()
    bootstrap_admin()
    yield


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.base_url.startswith("https://"),
)
app.mount("/static", StaticFiles(directory=base_dir / "static"), name="static")

FRONTEND_DIR = base_dir / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"
FRONTEND_ASSETS = FRONTEND_DIR / "assets"

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


@app.get("/manifest.json", include_in_schema=False)
def manifest_json():
    p = FRONTEND_DIR / "manifest.json"
    if p.exists():
        return FileResponse(p, media_type="application/manifest+json")
    raise HTTPException(status_code=404)


@app.get("/sw.js", include_in_schema=False)
def service_worker_js():
    p = FRONTEND_DIR / "sw.js"
    if p.exists():
        return FileResponse(p, media_type="application/javascript")
    raise HTTPException(status_code=404)


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    p = FRONTEND_DIR / "favicon.svg"
    if p.exists():
        return FileResponse(p, media_type="image/svg+xml")
    fav = base_dir / "static" / "favicon.svg"
    if fav.exists():
        return FileResponse(fav, media_type="image/svg+xml")
    raise HTTPException(status_code=404)


@app.get("/discover", response_class=HTMLResponse)
@app.get("/chat", response_class=HTMLResponse)
@app.get("/help", response_class=HTMLResponse)
@app.get("/digests", response_class=HTMLResponse)
def frontend_pages(request: Request):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return RedirectResponse("/")



@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    fav = base_dir / "static" / "favicon.svg"
    if fav.exists():
        return FileResponse(fav, media_type="image/svg+xml")
    return RedirectResponse("/static/favicon.svg")


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session["flash"] = {"message": message, "category": category}


def page_context(request: Request, **values):
    csrf = request.session.setdefault("csrf", new_csrf_token())
    return {
        "request": request,
        "app_name": settings.app_name,
        "current_user": getattr(request.state, "user", None),
        "csrf_token": csrf,
        "flash": request.session.pop("flash", None),
        **values,
    }


def check_csrf(request: Request, csrf_token: str) -> None:
    if not csrf_token or csrf_token != request.session.get("csrf"):
        raise HTTPException(status_code=403, detail="表单已过期，请刷新后重试")


def cancel_queued_deliveries(db: Session, user_id: int, account_id: int | None = None) -> None:
    item_ids = select(FeedItem.id)
    if account_id is not None:
        item_ids = item_ids.where(FeedItem.xueqiu_account_id == account_id)
    db.execute(
        update(Delivery)
        .where(
            Delivery.user_id == user_id,
            Delivery.feed_item_id.in_(item_ids),
            Delivery.status.in_(("pending", "retry")),
        )
        .values(status="cancelled", last_error="订阅已停用")
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_enabled:
        request.session.clear()
        raise HTTPException(status_code=401, detail="请先登录")
    request.state.user = user
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="无管理员权限")
    return user


# 订阅源平台白名单：防止任意 platform 触发未知抓取逻辑
VALID_SUBSCRIPTION_PLATFORMS = {"xueqiu", "xueqiu_cube", "weibo", "cls", "wallstreetcn", "gelonghui", "36kr", "jisilu", "wechat", "custom_rss"}
# 源标识/显示名称长度上限，与 XueqiuAccount 的 String(500) / String(120) 列宽对齐
SOURCE_KEY_MAX_LEN = 500
SOURCE_NAME_MAX_LEN = 120


def wechat_ingest_on() -> bool:
    """微信群接入功能是否已在后台开启（默认关闭）。"""
    from app import appconfig
    return str(appconfig.get_cfg("wechat_ingest_enabled", "false")).strip().lower() in ("true", "1", "yes", "on")


def has_control_char(value: str) -> bool:
    """是否含控制字符：换行/制表等会被拼进下游请求地址或模板，一律拒绝。"""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


class SubSourceCheck(NamedTuple):
    """校验结论。不通过时由调用方按自身风格呈现 message：表单页 flash，JSON 接口抛 HTTPException。"""

    ok: bool
    message: str = ""
    status_code: int = 400
    platform: str = ""
    source_key: str = ""
    display_name: str = ""


def check_subscription_source(
    *,
    platform: str,
    source_key: str,
    display_name: str = "",
    is_admin: bool = False,
) -> SubSourceCheck:
    """创建订阅/抓取源前的统一入参校验，返回规范化后的 platform / source_key / display_name。

    所有会写入 XueqiuAccount 或 Subscription 的入口（表单 POST /subscriptions、
    JSON POST /api/subscriptions、JSON POST /api/discover/subscribe）都必须调用本函数，
    避免新增入口漏掉校验。custom_rss 的 source_key 会被直接当作抓取地址，因此仅限管理员；
    地址本身能否访问仍由 services.fetcher._validate_custom_rss_url 判定。
    """
    clean_platform = (platform or "").strip().lower()
    if clean_platform not in VALID_SUBSCRIPTION_PLATFORMS:
        return SubSourceCheck(False, "不支持的平台类型", 400)
    if clean_platform == "custom_rss" and not is_admin:
        return SubSourceCheck(False, "自定义 RSS 源仅管理员可用", 403)
    if clean_platform == "wechat" and not wechat_ingest_on():
        return SubSourceCheck(False, "微信群接入功能当前未启用", 400)
    clean_key = (source_key or "").strip()
    clean_name = (display_name or "").strip()
    if (
        not clean_key or len(clean_key) > SOURCE_KEY_MAX_LEN or has_control_char(clean_key)
        or not clean_name or len(clean_name) > SOURCE_NAME_MAX_LEN or has_control_char(clean_name)
    ):
        return SubSourceCheck(False, "请填写有效的用户 UID/链接和显示名称", 400)
    return SubSourceCheck(True, platform=clean_platform, source_key=clean_key, display_name=clean_name)


@app.exception_handler(401)
async def unauthorized(request: Request, _exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "请先登录"}, status_code=401)
    flash(request, "请先登录", "error")
    return RedirectResponse("/login", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", page_context(request))


REGISTER_ANSWER = "避难所2026"


def effective_register_answer() -> str:
    """注册群名答案：管理后台可动态修改（app_config.register_answer），未配置时回退内置值。"""
    from app import appconfig

    return str(appconfig.get_cfg("register_answer", REGISTER_ANSWER)).strip()


# 登录接口的简易内存限速：同一来源 IP 在时间窗口内连续失败达到阈值后临时锁定
_LOGIN_FAIL_WINDOW_SECONDS = 600.0
_LOGIN_MAX_FAILS = 8
_login_fails: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def login_is_blocked(ip: str) -> bool:
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_FAIL_WINDOW_SECONDS]
    _login_fails[ip] = fails
    return len(fails) >= _LOGIN_MAX_FAILS


def record_login_fail(ip: str) -> None:
    _login_fails.setdefault(ip, []).append(time.time())


def clear_login_fails(ip: str) -> None:
    _login_fails.pop(ip, None)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "register.html", page_context(request))


@app.post("/register")
def register(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(),
    password: str = Form(),
    confirm: str = Form(""),
    email: str = Form(""),
    answer: str = Form(""),
    csrf_token: str = Form(),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    ip = client_ip(request)
    if login_is_blocked(f"reg:{ip}"):
        flash(request, "尝试次数过多，请 10 分钟后再试", "error")
        return RedirectResponse("/register", status_code=303)
    username = username.strip()
    email = email.strip().lower()
    answer = answer.strip()

    if not username:
        flash(request, "请填写用户名", "error")
        return RedirectResponse("/register", status_code=303)
    if len(username) < 3 or len(username) > 32:
        flash(request, "用户名长度需为 3-32 个字符", "error")
        return RedirectResponse("/register", status_code=303)
    if not password or len(password) < 6:
        flash(request, "密码至少 6 位", "error")
        return RedirectResponse("/register", status_code=303)
    if password != confirm:
        flash(request, "两次输入的密码不一致", "error")
        return RedirectResponse("/register", status_code=303)
    if answer != effective_register_answer():
        record_login_fail(f"reg:{ip}")
        flash(request, "群名答案不正确", "error")
        return RedirectResponse("/register", status_code=303)
    if email and not validate_email(email, check_deliverability=False).email:
        flash(request, "邮箱格式不正确", "error")
        return RedirectResponse("/register", status_code=303)

    if db.scalar(select(User.id).where(User.username == username)):
        flash(request, "该用户名已被注册", "error")
        return RedirectResponse("/register", status_code=303)
    if email and db.scalar(select(User.id).where(User.email == email)):
        flash(request, "该邮箱已被注册", "error")
        return RedirectResponse("/register", status_code=303)

    # 填写了邮箱的注册需要邮箱验证（防止伪造他人邮箱注册）；
    # 未填邮箱的本地账号直接放行（仅可配合群机器人 Webhook 使用）
    using_email = bool(email)
    user = User(
        username=username,
        email=email or f"{username}@local",
        password_hash=hash_password(password),
        email_verified=not using_email,
        is_enabled=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "用户名或邮箱冲突，请换一个", "error")
        return RedirectResponse("/register", status_code=303)

    if using_email:
        background_tasks.add_task(
            send_verification_email, user.email, make_token(user.id, "verify-email")
        )
        flash(request, "注册成功，验证邮件已发送，请查收后登录", "success")
    else:
        flash(request, "注册成功，请登录", "success")
    return RedirectResponse("/login", status_code=303)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    csrf_token: str = Form(),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    ip = client_ip(request)
    if login_is_blocked(ip):
        flash(request, "登录失败次数过多，请 10 分钟后再试", "error")
        return RedirectResponse("/login", status_code=303)
    ident = email.strip()
    user = db.scalar(
        select(User).where(
            (User.email == ident.lower()) | (User.username == ident)
        )
    )
    if not user or not verify_password(password, user.password_hash):
        record_login_fail(ip)
        flash(request, "用户名/邮箱或密码错误", "error")
        return RedirectResponse("/login", status_code=303)
    if not user.is_enabled:
        record_login_fail(ip)
        flash(request, "账号已停用", "error")
        return RedirectResponse("/login", status_code=303)
    if not user.email_verified:
        flash(request, "请先完成邮箱验证", "error")
        return RedirectResponse("/login", status_code=303)
    clear_login_fails(ip)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf"] = new_csrf_token()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form()):
    check_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    user = current_user(request, db)
    subscriptions = db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.account))
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.desc())
    ).all()
    has_custom_smtp = bool(user.smtp_host and user.smtp_username)
    from app import appconfig
    wechat_ingest_enabled = str(appconfig.get_cfg("wechat_ingest_enabled", "false")).strip().lower() in ("true", "1", "yes", "on")
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        page_context(request, subscriptions=subscriptions, has_custom_smtp=has_custom_smtp, has_webhook=bool(user.webhook_enabled and user.webhook_url), wechat_ingest_enabled=wechat_ingest_enabled),
    )



from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)


@app.get("/chat", response_class=HTMLResponse)
@app.get("/research", response_class=HTMLResponse)
@app.get("/timeline", response_class=HTMLResponse)
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    user = current_user(request, db)
    return templates.TemplateResponse(request, "chat.html", page_context(request))


@app.get("/api/chat/history")
def api_chat_history(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    import json
    from app.models import ChatMessage
    # 取最近的 50 条再反转为正序，避免记录增多后新消息被挤出历史
    msgs = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.id.desc())
        .limit(50)
    ).all()
    msgs.reverse()
    history = []
    for m in msgs:
        try:
            sources = json.loads(m.sources_json) if m.sources_json else []
            if not isinstance(sources, list):
                sources = []
        except (ValueError, TypeError):
            sources = []
        history.append({
            "id": m.id,
            "query": m.query,
            "answer": m.answer,
            "sources": sources,
            "created_at": m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
        })
    return {"history": history}


@app.post("/api/chat")
def api_chat(
    req: ChatRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    import json
    from app.models import ChatMessage
    from app.rag_service import ask_financial_copilot
    query = req.query.strip()
    if not query:
        return {"answer": "请输入您的问题。", "sources": []}
    res = ask_financial_copilot(db, user, query)
    if res.get("answer") and not res.get("answer").startswith("未配置大模型"):
        try:
            msg = ChatMessage(
                user_id=user.id,
                query=query,
                answer=res["answer"],
                sources_json=json.dumps(res.get("sources", []), ensure_ascii=False),
            )
            db.add(msg)
            db.commit()
        except Exception as e:
            logger.warning("保存聊天历史记录失败: %s", e)
    return res


@app.post("/api/chat/clear")
def api_chat_clear(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import delete
    from app.models import ChatMessage
    db.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id))
    db.commit()
    return {"ok": True}



FEATURED_SOURCES = [
    {
        "id": "duanyongping",
        "name": "段永平 (大道无形我有型)",
        "platform": "xueqiu",
        "platform_name": "雪球",
        "xueqiu_user_id": "8249826314",
        "category": "value",
        "category_name": "价值投资",
        "badge_color": "#2563eb",
        "tag": "千亿级实战买家 · 商业逻辑与本分哲学",
        "desc": "步步高创始人，著名投资家。聚焦苹果、茅台、腾讯核心资产，强调“买股票就是买公司”、“做对的事情，把事情做对”。",
    },
    {
        "id": "danbin",
        "name": "但斌 (东方港湾)",
        "platform": "weibo",
        "platform_name": "微博财经",
        "xueqiu_user_id": "1198642231",
        "category": "value",
        "category_name": "价值投资",
        "badge_color": "#e11d48",
        "tag": "东方港湾董事长 · 龙头白马与AI时代",
        "desc": "《时间的玫瑰》作者，中国价值投资先行者。长期聚焦全球AI科技巨头与消费品核心资产。",
    },
    {
        "id": "mangerchen",
        "name": "芒格书院",
        "platform": "xueqiu",
        "platform_name": "雪球",
        "xueqiu_user_id": "8116560417",
        "category": "value",
        "category_name": "价值投资",
        "badge_color": "#059669",
        "tag": "格雷厄姆与芒格智库 · 多元思维模型",
        "desc": "专注于查理·芒格普世智慧、跨学科思维模型与巴菲特股东信深度导读。",
    },
    {
        "id": "renzeping",
        "name": "任泽平",
        "platform": "weibo",
        "platform_name": "微博财经",
        "xueqiu_user_id": "1111681197",
        "category": "macro",
        "category_name": "宏观策略",
        "badge_color": "#d97706",
        "tag": "清华大学博士 · 宏观经济与周期论",
        "desc": "原国务院发展研究中心宏观部研究室副主任，著名经济学家。聚焦货币财政、房地产周期与新质生产力。",
    },
    {
        "id": "honghao",
        "name": "洪灏",
        "platform": "weibo",
        "platform_name": "微博财经",
        "xueqiu_user_id": "1932468783",
        "category": "macro",
        "category_name": "宏观策略",
        "badge_color": "#7c3aed",
        "tag": "思睿集团首席经济学家 · 预测帝",
        "desc": "原交银国际研究部主管，全球市场配置与量化周期预测专家，精准预判多次市场顶部与拐点。",
    },
    {
        "id": "laomujiang",
        "name": "买股票的老木匠",
        "platform": "xueqiu",
        "platform_name": "雪球",
        "xueqiu_user_id": "3058599833",
        "category": "cycle",
        "category_name": "周期实战",
        "badge_color": "#0284c7",
        "tag": "周期认知与交易体系 · 语录评书",
        "desc": "雪球资深高互动大V，擅长低买高卖周期思维、前复权折现估值与组合回撤控制实战。",
    },
    {
        "id": "cls_telegraph",
        "name": "财联社7×24电报快讯",
        "platform": "cls",
        "platform_name": "财联社",
        "xueqiu_user_id": "cls",
        "category": "news",
        "category_name": "7×24快讯",
        "badge_color": "#dc2626",
        "tag": "A股秒级一手电报 · 监管与盘中异动",
        "desc": "全天候无休监控国内外财经要闻、政策法规落地、上市公司公告与行业突发异动。",
    },
    {
        "id": "wallstreetcn_global",
        "name": "华尔街见闻全球快讯",
        "platform": "wallstreetcn",
        "platform_name": "华尔街见闻",
        "xueqiu_user_id": "wallstreetcn",
        "category": "news",
        "category_name": "7×24快讯",
        "badge_color": "#2563eb",
        "tag": "全球宏观对冲 · 美联储与外汇大宗",
        "desc": "聚焦全球央行议息决议、非农通胀CPI、美股美债与国际大宗商品异动快报。",
    },
    {
        "id": "gelonghui_live",
        "name": "格隆汇7×24财经电报",
        "platform": "gelonghui",
        "platform_name": "格隆汇",
        "xueqiu_user_id": "gelonghui",
        "category": "news",
        "category_name": "7×24快讯",
        "badge_color": "#059669",
        "tag": "港美股跨境投资 · 深度商业研报",
        "desc": "大中华区顶尖跨境投研平台，7×24小时紧密追踪港股、美股与中概股最新动向。",
    },
    {
        "id": "36kr_flashes",
        "name": "36氪前沿快讯",
        "platform": "36kr",
        "platform_name": "36氪",
        "xueqiu_user_id": "36kr",
        "category": "news",
        "category_name": "7×24快讯",
        "badge_color": "#0284c7",
        "tag": "新经济与硬科技 · 创投大模型",
        "desc": "专注硬科技、大模型AI浪潮、独角兽企业投融资与前沿消费科技商业动态。",
    },
    {
        "id": "jisilu_explore",
        "name": "集思录实时精选广场",
        "platform": "jisilu",
        "platform_name": "集思录",
        "xueqiu_user_id": "preset_jisilu",
        "category": "arbitrage",
        "category_name": "套利转债",
        "badge_color": "#ea580c",
        "tag": "低风险投资 · 可转债与套利",
        "desc": "中国顶尖低风险投资理财社区，汇聚可转债强赎下修、分级A/LOF套利与高股息策略讨论。",
    },
]


@app.get("/api/discover/sources")
def api_discover_sources(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.models import Subscription
    subs = db.scalars(
        select(Subscription)
        .join(Subscription.account)
        .where(Subscription.user_id == user.id)
    ).all()
    subscribed_keys = {
        (s.account.platform, s.account.xueqiu_user_id)
        for s in subs if s.account
    }

    featured_with_status = []
    for item in FEATURED_SOURCES:
        it = dict(item)
        it["is_subscribed"] = (it["platform"], it["xueqiu_user_id"]) in subscribed_keys
        featured_with_status.append(it)

    return {"sources": featured_with_status}

@app.get("/discover", response_class=HTMLResponse)
def discover_page(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.models import Subscription, XueqiuAccount
    subs = db.scalars(
        select(Subscription)
        .join(Subscription.account)
        .where(Subscription.user_id == user.id)
    ).all()
    subscribed_keys = {
        (s.account.platform, s.account.xueqiu_user_id)
        for s in subs if s.account
    }

    featured_with_status = []
    for item in FEATURED_SOURCES:
        it = dict(item)
        it["is_subscribed"] = (it["platform"], it["xueqiu_user_id"]) in subscribed_keys
        featured_with_status.append(it)

    ctx = page_context(request)
    ctx.update({
        "featured_sources": featured_with_status,
        "current_user": user,
    })
    return templates.TemplateResponse(request, "discover.html", ctx)


class DiscoverSubscribeRequest(BaseModel):
    platform: str
    xueqiu_user_id: str
    display_name: str


@app.post("/api/discover/subscribe")
def api_discover_subscribe(
    req: DiscoverSubscribeRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.models import XueqiuAccount, Subscription
    # 推荐源入口对所有登录用户开放，必须走统一的源校验：custom_rss 仅限管理员，
    # 否则普通用户可用 platform=custom_rss + 任意 URL 绕过后台闸门，把服务器变成任意外呼抓取器
    checked = check_subscription_source(
        platform=req.platform,
        source_key=req.xueqiu_user_id,
        display_name=req.display_name,
        is_admin=user.is_admin,
    )
    if not checked.ok:
        raise HTTPException(status_code=checked.status_code, detail=checked.message)
    platform, source_key, display_name = checked.platform, checked.source_key, checked.display_name
    acc = db.scalar(
        select(XueqiuAccount).where(
            XueqiuAccount.platform == platform,
            XueqiuAccount.xueqiu_user_id == source_key,
        )
    )
    if not acc:
        acc = XueqiuAccount(
            platform=platform,
            xueqiu_user_id=source_key,
            display_name=display_name,
            # 与 /api/subscriptions 的默认动态类型保持一致（原先沿用列默认值 all）
            feed_type="10",
        )
        db.add(acc)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"ok": False, "message": "创建抓取源失败：源标识冲突，请稍后重试"}

    sub = db.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.xueqiu_account_id == acc.id,
        )
    )
    if not sub:
        sub = Subscription(
            user_id=user.id,
            xueqiu_account_id=acc.id,
            is_enabled=True,
            delivery_mode="immediate",
            delivery_channel="both",
            ai_summary_enabled=True,
        )
        db.add(sub)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"ok": False, "message": "关注失败：订阅记录冲突，请刷新后重试"}
        return {"ok": True, "message": f"成功关注「{display_name}」！", "sub_id": sub.id}
    else:
        sub.is_enabled = True
        db.commit()
        return {"ok": True, "message": f"「{display_name}」已重新开启监控！", "sub_id": sub.id}



class WechatIngestRequest(BaseModel):
    group_name: str = Field(min_length=1, max_length=100)
    sender_name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    group_id: str | None = Field(default=None, max_length=200)
    sender_id: str | None = Field(default=None, max_length=200)
    msg_type: str = Field(default="text", max_length=32)
    msg_id: str | None = Field(default=None, max_length=200)
    timestamp: int | None = None


def _deliver_wechat_messages() -> None:
    """请求返回后异步执行即时投递，避免 SMTP/Webhook 重试长时间阻塞 HTTP 响应。"""
    from app.database import SessionLocal
    from app.services import send_immediate_deliveries

    try:
        with SessionLocal() as task_db:
            send_immediate_deliveries(task_db)
    except Exception as exc:
        logger.warning("微信群消息即时投递异常: %s", exc)


@app.post("/api/wechat/ingest")
def api_wechat_ingest(
    req: WechatIngestRequest,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
    x_wechat_token: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """接收微信小号/网关转发的群聊发言，精准匹配关注的目标大牛并秒级分发投递。"""
    import hashlib
    import hmac
    import time
    from app import appconfig
    from app.models import XueqiuAccount, Subscription, FeedItem, Delivery, User
    from app.services import is_item_allowed_by_subscription_filter

    # 0. 功能开关：微信网关默认关闭（后台「系统运行设置」可开启）；关闭时对外表现为路由不存在
    if str(appconfig.get_cfg("wechat_ingest_enabled", "false")).strip().lower() not in ("true", "1", "yes", "on"):
        raise HTTPException(status_code=404, detail="Not Found")

    # 1. 认证鉴权：令牌必须由管理员在后台显式配置（app_config.wechat_ingest_token），
    #    不再提供代码内默认值，避免源码泄露即被伪造消息注入
    configured_token = str(appconfig.get_cfg("wechat_ingest_token", "")).strip()
    if not configured_token:
        raise HTTPException(status_code=503, detail="微信接入未配置安全令牌，请联系管理员在后台配置")
    provided_token = None
    if authorization and authorization.startswith("Bearer "):
        provided_token = authorization[7:].strip()
    elif x_wechat_token:
        provided_token = x_wechat_token.strip()

    if not provided_token or not hmac.compare_digest(provided_token, configured_token):
        raise HTTPException(status_code=403, detail="微信接入安全令牌无效或缺失")

    clean_content = req.content.strip()
    if not clean_content:
        return {"ok": False, "message": "消息正文为空"}

    group_name = req.group_name.strip()
    sender_name = req.sender_name.strip() or "匿名群友"
    account_key = f"{group_name}:{sender_name}"
    display_title = f"【{group_name}】{sender_name}"

    # 2. 匹配或自动建源 XueqiuAccount
    acc = db.scalar(
        select(XueqiuAccount).where(
            XueqiuAccount.platform == "wechat",
            XueqiuAccount.xueqiu_user_id == account_key,
        )
    )
    if not acc:
        acc = XueqiuAccount(
            platform="wechat",
            xueqiu_user_id=account_key,
            display_name=display_title,
            initialized=True,
            last_checked_at=datetime.now(timezone.utc),
        )
        db.add(acc)
        db.flush()

    # 3. 查重并入库 FeedItem
    item_key = req.msg_id or hashlib.md5(f"{account_key}_{clean_content}_{req.timestamp or time.time()}".encode()).hexdigest()
    existing_item = db.scalar(
        select(FeedItem).where(
            FeedItem.xueqiu_account_id == acc.id,
            FeedItem.item_key == item_key,
        )
    )
    if existing_item:
        return {"ok": True, "message": "重复消息，已跳过入库", "item_id": existing_item.id}

    # 时间戳范围校验：偏离当前时间超过 7 天视为异常，回退为接收时间
    if req.timestamp and abs(time.time() - int(req.timestamp)) <= 7 * 86400:
        pub_time = datetime.fromtimestamp(int(req.timestamp), tz=timezone.utc)
    else:
        if req.timestamp:
            logger.warning("微信群消息时间戳异常（%s），已回退为接收时间", req.timestamp)
        pub_time = datetime.now(timezone.utc)
    feed_item = FeedItem(
        xueqiu_account_id=acc.id,
        item_key=item_key,
        title=clean_content[:36].replace("\n", " ") + ("..." if len(clean_content) > 36 else ""),
        content=clean_content,
        link="",
        published_at=pub_time,
    )
    db.add(feed_item)
    db.flush()

    # 4. 匹配关注此大牛或关注该群全部人的订阅
    target_keys = [account_key, f"{group_name}:ALL", f"{group_name}:"]
    matching_account_ids = db.scalars(
        select(XueqiuAccount.id).where(
            XueqiuAccount.platform == "wechat",
            XueqiuAccount.xueqiu_user_id.in_(target_keys),
        )
    ).all()

    subscriptions = db.scalars(
        select(Subscription)
        .join(User)
        .where(
            Subscription.xueqiu_account_id.in_(matching_account_ids),
            Subscription.is_enabled.is_(True),
            User.is_enabled.is_(True),
            User.email_verified.is_(True),
        )
    ).all()

    deliveries_created = 0
    for sub in subscriptions:
        allowed, reason = is_item_allowed_by_subscription_filter(feed_item, sub)
        if not allowed:
            logger.info("用户 %s 过滤拦截微信群发言 [%s]: %s", sub.user_id, feed_item.title[:20], reason)
            continue
        db.add(Delivery(
            feed_item_id=feed_item.id,
            user_id=sub.user_id,
            delivery_mode=sub.delivery_mode,
        ))
        deliveries_created += 1

    db.commit()

    # 5. 若有待投递任务，请求返回后异步触发即时推送（跨进程由投递锁互斥）
    if deliveries_created > 0:
        background_tasks.add_task(_deliver_wechat_messages)

    return {
        "ok": True,
        "message": "微信群消息已成功接收并投递",
        "account": display_title,
        "feed_item_id": feed_item.id,
        "deliveries_queued": deliveries_created,
    }


@app.get("/api/subscriptions/{subscription_id}/filter")
def api_get_subscription_filter(
    subscription_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.models import Subscription
    sub = db.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user.id,
        )
    )
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {
        "ok": True,
        "keywords_include": sub.keywords_include or "",
        "keywords_exclude": sub.keywords_exclude or "",
        "alert_keywords": sub.alert_keywords or "",
        "filter_min_length": sub.filter_min_length or 0,
    }


class FilterUpdateRequest(BaseModel):
    keywords_include: str | None = None
    keywords_exclude: str | None = None
    alert_keywords: str | None = None
    filter_min_length: int = 0


@app.post("/api/subscriptions/{subscription_id}/filter")
def api_update_subscription_filter(
    subscription_id: int,
    req: FilterUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.models import Subscription
    sub = db.scalar(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user.id,
        )
    )
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    sub.keywords_include = (req.keywords_include or "").strip() or None
    sub.keywords_exclude = (req.keywords_exclude or "").strip() or None
    sub.alert_keywords = (req.alert_keywords or "").strip()[:512] or None
    sub.filter_min_length = max(0, int(req.filter_min_length or 0))
    db.commit()
    return {"ok": True, "message": "降噪、强提醒与过滤规则已保存生效！"}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    user = current_user(request, db)
    from email.utils import parseaddr
    display_from = user.smtp_from or ""
    if display_from and "<" in display_from and ">" in display_from:
        parsed_name, _ = parseaddr(display_from)
        if parsed_name:
            display_from = parsed_name

    from app import appconfig
    global_llm_enabled = str(appconfig.get_cfg("llm_enabled", str(settings.llm_enabled))).lower() in ("true", "1", "yes", "on")
    global_llm_model = str(appconfig.get_cfg("llm_model", settings.llm_model))

    return templates.TemplateResponse(
        request,
        "settings.html",
        page_context(request, smtp={
            "host": user.smtp_host or "",
            "port": user.smtp_port or "",
            "username": user.smtp_username or "",
            "from": display_from,
            "ssl": user.smtp_ssl if user.smtp_ssl is not None else True,
            "starttls": user.smtp_starttls if user.smtp_starttls is not None else False,
        }, llm={
            "enabled": user.llm_enabled if user.llm_enabled is not None else global_llm_enabled,
            "user_custom": bool(user.llm_api_base and user.llm_api_key),
            "api_base": user.llm_api_base or "",
            "model": user.llm_model or "",
            "has_key": bool(user.llm_api_key),
            "global_enabled": global_llm_enabled,
            "global_model": global_llm_model,
        }, webhook={
            "enabled": bool(user.webhook_enabled),
            "type": user.webhook_type or "auto",
            "url": "",
            "secret": "",
            "has_url": bool(user.webhook_url),
            "has_secret": bool(user.webhook_secret),
        }),
    )


@app.get("/help", response_class=HTMLResponse)
def help_page(
    request: Request,
    user: User = Depends(current_user),
):
    return templates.TemplateResponse(request, "help.html", page_context(request))


@app.post("/settings")
def save_settings(
    request: Request,
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_ssl: str | None = Form(None),
    smtp_starttls: str | None = Form(None),
    llm_enabled: str | None = Form(None),
    llm_api_base: str = Form(""),
    llm_api_key: str = Form(""),
    llm_model: str = Form(""),
    webhook_enabled: str | None = Form(None),
    webhook_type: str = Form("auto"),
    webhook_url: str = Form(""),
    webhook_secret: str = Form(""),
    action: str = Form(""),
    section: str = Form(""),
    csrf_token: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    # 分区保存：SMTP / AI / Webhook 三个表单各自只更新自己的字段，
    # 避免提交其中一个表单时把其他区块的开关状态意外重置
    if not section:
        section = "webhook" if action in ("delete_webhook", "test_webhook") else ("llm" if action == "delete_llm" else "smtp")
    if section == "webhook":
        _save_webhook_section(request, user, db, action=action, webhook_enabled=webhook_enabled,
                              webhook_type=webhook_type, webhook_url=webhook_url, webhook_secret=webhook_secret)
        db.commit()
        if action == "test_webhook":
            return _test_webhook(request, user)
        if action != "delete_webhook":
            flash(request, "群机器人 Webhook 通道配置已保存。", "success")
        return RedirectResponse("/settings", status_code=303)
    if section == "llm":
        if action == "delete_llm":
            user.llm_api_base = None
            user.llm_api_key = None
            user.llm_model = None
            flash(request, "已清除自定义大模型配置，将使用系统全局大模型。", "success")
        else:
            user.llm_enabled = bool(llm_enabled)
            llm_api_base = llm_api_base.strip()
            llm_model = llm_model.strip()
            llm_api_key = llm_api_key.strip()
            if llm_api_base:
                user.llm_api_base = llm_api_base
            if llm_model:
                user.llm_model = llm_model
            # 掩码哨兵 = 保持原 Key 不变，空值同样不动（本页清除走 action=delete_llm）
            if is_new_secret(llm_api_key):
                user.llm_api_key = encrypt_secret(llm_api_key, settings.secret_key)
            flash(request, "AI 模型配置已保存。", "success")
        db.commit()
        return RedirectResponse("/settings", status_code=303)

    # 清除配置：清空用户级 SMTP 字段，回退到系统默认发信账号
    if action == "delete":
        user.smtp_host = None
        user.smtp_port = None
        user.smtp_username = None
        user.smtp_password = None
        user.smtp_from = None
        user.smtp_ssl = None
        user.smtp_starttls = None
        db.commit()
        flash(request, "邮箱发信配置已清除，将使用系统默认发信账号。", "success")
        return RedirectResponse("/settings", status_code=303)
    smtp_host = smtp_host.strip()
    smtp_username = smtp_username.strip()
    smtp_from = smtp_from.strip()
    if smtp_from:
        from email.utils import parseaddr
        name, addr = parseaddr(smtp_from)
        if name:
            smtp_from = name
        elif addr and "@" in addr:
            # 误填纯邮箱地址时自动清空纯名称，由系统自动使用发信账号
            smtp_from = ""
    # 端口解析
    port = None
    if smtp_port.strip():
        try:
            port = int(smtp_port)
        except ValueError:
            flash(request, "端口必须是数字", "error")
            return RedirectResponse("/settings", status_code=303)
    # 若填了 host 或 username，则视为启用自管 SMTP，要求关键项齐全
    if smtp_host or smtp_username:
        if not smtp_host or not smtp_username:
            flash(request, "启用自管发信需填写 SMTP 服务器和登录账号", "error")
            return RedirectResponse("/settings", status_code=303)
        # 已保存过配置且本次未改密码时，保留原密码；否则必须填写
        if not smtp_password and not user.smtp_password:
            flash(request, "请填写 SMTP 授权码（密码）", "error")
            return RedirectResponse("/settings", status_code=303)
    # 保存
    user.smtp_host = smtp_host or None
    user.smtp_port = port
    user.smtp_username = smtp_username or None
    # 密码留空则保留已保存的密码（修改其他字段时不强制重填）；加密落盘（兼容读取旧明文）
    # 收到掩码哨兵时同样保持原值，绝不清除
    if is_new_secret(smtp_password):
        user.smtp_password = encrypt_secret(smtp_password, settings.secret_key)
    user.smtp_from = smtp_from or None
    user.smtp_ssl = bool(smtp_ssl)
    user.smtp_starttls = bool(smtp_starttls)
    db.commit()
    flash(request, "邮箱发信配置已保存。", "success")
    return RedirectResponse("/settings", status_code=303)


def _save_webhook_section(request: Request, user: User, db: Session, action: str,
                          webhook_enabled: str | None, webhook_type: str,
                          webhook_url: str, webhook_secret: str) -> None:
    """仅更新 Webhook 相关字段；URL/Secret 留空表示保持原值，加密落盘。"""
    if action == "delete_webhook":
        user.webhook_enabled = False
        user.webhook_type = None
        user.webhook_url = None
        user.webhook_secret = None
        flash(request, "群机器人 Webhook 配置已清除。", "success")
        return
    user.webhook_enabled = bool(webhook_enabled)
    user.webhook_type = webhook_type.strip() or "auto"
    # 留空或回传掩码哨兵都表示保持原值；清除走 action=delete_webhook
    if is_new_secret(webhook_url):
        user.webhook_url = encrypt_secret(webhook_url.strip(), settings.secret_key)
    if is_new_secret(webhook_secret):
        user.webhook_secret = encrypt_secret(webhook_secret.strip(), settings.secret_key)


def _test_webhook(request: Request, user: User):
    from app.webhook import send_user_webhook
    ok = send_user_webhook(
        user,
        "群机器人 Webhook 测试消息",
        "🎉 恭喜！群机器人 Webhook 通道测试成功！\n\n系统已成功打通群通知推送，后续博主动态与每日 AI 投研内参将自动同步到本群。",
        link=settings.base_url,
    )
    if ok:
        flash(request, "已向群机器人发送测试消息，请在群聊中查收！", "success")
    else:
        flash(request, "群机器人测试发送失败，请检查 Webhook 链接或密钥是否正确。", "error")
    return RedirectResponse("/settings", status_code=303)


@app.post("/subscriptions")
def add_subscription(
    request: Request,
    xueqiu_user_id: str = Form(),
    display_name: str = Form(),
    platform: str = Form("xueqiu"),
    feed_type: str = Form("all"),
    delivery_mode: str = Form("immediate"),
    delivery_channel: str = Form("both"),
    ai_summary_enabled: str | None = Form("on"),
    csrf_token: str = Form(),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    xueqiu_user_id = xueqiu_user_id.strip()
    display_name = display_name.strip()
    if platform.strip().lower() == "wechat":
        # 微信群按「群名:发言人」组合源标识，需在校验前拼好（长度上限作用于组合后的标识）
        group_name = xueqiu_user_id
        speaker = display_name or "ALL"
        xueqiu_user_id = f"{group_name}:{speaker}"
        display_name = f"【{group_name}】{speaker if speaker != 'ALL' else '全部成员'}"
    # 平台白名单 / custom_rss 管理员闸门 / 微信接入开关 / 源标识合法性：统一由 check_subscription_source 校验
    checked = check_subscription_source(
        platform=platform,
        source_key=xueqiu_user_id,
        display_name=display_name,
        is_admin=user.is_admin,
    )
    if not checked.ok:
        flash(request, checked.message, "error")
        return RedirectResponse("/", status_code=303)
    platform = checked.platform
    xueqiu_user_id = checked.source_key
    display_name = checked.display_name
    feed_type = feed_type.strip().lower() or "all"
    # 雪球 user_timeline 的 type 参数：all 等价于 10（全部）
    if feed_type in {"all", "10"}:
        feed_type = "10"
    if feed_type not in {"10", "0", "2", "4", "9", "11"}:
        flash(request, "无效的 Feed 类型", "error")
        return RedirectResponse("/", status_code=303)
    if delivery_mode not in {"immediate", "daily"}:
        delivery_mode = "immediate"
    has_realtime = bool((user.smtp_host and user.smtp_username) or (user.webhook_enabled and user.webhook_url))
    if delivery_mode == "immediate" and not has_realtime:
        delivery_mode = "daily"
    
    # 查找或创建抓取源（支持多平台同ID共存）
    account = db.scalar(select(XueqiuAccount).where(
        XueqiuAccount.platform == platform,
        XueqiuAccount.xueqiu_user_id == xueqiu_user_id,
    ))
    if not account:
        account = XueqiuAccount(
            xueqiu_user_id=xueqiu_user_id,
            display_name=display_name,
            feed_type=feed_type,
            platform=platform,
        )
        db.add(account)
        db.flush()
    else:
        if platform and account.platform != platform:
            account.platform = platform
        if display_name:
            account.display_name = display_name
    existing = db.scalar(select(Subscription).where(
        Subscription.user_id == user.id,
        Subscription.xueqiu_account_id == account.id,
    ))
    ai_enabled = bool(ai_summary_enabled)
    if delivery_channel not in {"both", "webhook", "email"}:
        delivery_channel = "both"
    if existing:
        existing.is_enabled = True
        existing.delivery_mode = delivery_mode
        existing.delivery_channel = delivery_channel
        existing.ai_summary_enabled = ai_enabled
    else:
        db.add(Subscription(
            user_id=user.id,
            xueqiu_account_id=account.id,
            delivery_mode=delivery_mode,
            delivery_channel=delivery_channel,
            ai_summary_enabled=ai_enabled,
        ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "订阅保存失败：该抓取源刚被其他账号创建，请刷新后重试", "error")
        return RedirectResponse("/", status_code=303)
    flash(request, "订阅已保存。首次抓取只建立基线，不发送历史动态。", "success")
    return RedirectResponse("/", status_code=303)


@app.post("/subscriptions/{subscription_id}/update")
def update_subscription(
    subscription_id: int,
    request: Request,
    delivery_mode: str = Form("immediate"),
    delivery_channel: str = Form("both"),
    is_enabled: str | None = Form(None),
    ai_summary_enabled: str | None = Form(None),
    csrf_token: str = Form(),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    subscription = db.scalar(select(Subscription).where(
        Subscription.id == subscription_id,
        Subscription.user_id == user.id,
    ))
    if not subscription:
        raise HTTPException(status_code=404)
    if delivery_mode not in {"immediate", "daily"}:
        raise HTTPException(status_code=400)
    has_realtime = bool((user.smtp_host and user.smtp_username) or (user.webhook_enabled and user.webhook_url))
    if delivery_mode == "immediate" and not has_realtime:
        delivery_mode = "daily"
    subscription.delivery_mode = delivery_mode
    if delivery_channel not in {"both", "webhook", "email"}:
        delivery_channel = "both"
    subscription.delivery_channel = delivery_channel
    subscription.ai_summary_enabled = ai_summary_enabled == "on"
    subscription.is_enabled = is_enabled == "on"
    if subscription.is_enabled:
        item_ids = select(FeedItem.id).where(FeedItem.xueqiu_account_id == subscription.xueqiu_account_id)
        db.execute(
            update(Delivery)
            .where(
                Delivery.user_id == user.id,
                Delivery.feed_item_id.in_(item_ids),
                Delivery.status.in_(("pending", "retry")),
            )
            .values(delivery_mode=delivery_mode)
        )
    else:
        cancel_queued_deliveries(db, user.id, subscription.xueqiu_account_id)
    db.commit()
    flash(request, "订阅设置已更新", "success")
    return RedirectResponse("/", status_code=303)


@app.post("/subscriptions/{subscription_id}/delete")
def delete_subscription(
    subscription_id: int,
    request: Request,
    csrf_token: str = Form(),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    subscription = db.scalar(select(Subscription).where(
        Subscription.id == subscription_id,
        Subscription.user_id == user.id,
    ))
    if subscription:
        cancel_queued_deliveries(db, user.id, subscription.xueqiu_account_id)
        db.delete(subscription)
        db.commit()
    flash(request, "订阅已删除", "success")
    return RedirectResponse("/", status_code=303)


@app.get("/verify/{token}", response_class=HTMLResponse)
def verify_email(token: str, request: Request, db: Session = Depends(get_db)):
    user_id = read_token(token, "verify-email", max_age=7 * 24 * 3600)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_enabled:
        return templates.TemplateResponse(
            request, "message.html", page_context(request, title="链接无效", message="验证链接无效或已过期。"), status_code=400
        )
    user.email_verified = True
    db.commit()
    flash(request, "邮箱验证成功，现在可以登录", "success")
    return RedirectResponse("/login", status_code=303)


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, request: Request, db: Session = Depends(get_db)):
    # 退订链接有效期 180 天：足够覆盖邮件留存期，又避免被长期滥用
    user_id = read_token(token, "unsubscribe", max_age=180 * 24 * 3600)
    user = db.get(User, user_id) if user_id else None
    if not user:
        return templates.TemplateResponse(
            request, "message.html", page_context(request, title="链接无效", message="退订链接无效。"), status_code=400
        )
    db.query(Subscription).filter(Subscription.user_id == user.id).update({"is_enabled": False})
    cancel_queued_deliveries(db, user.id)
    db.commit()
    return templates.TemplateResponse(
        request, "message.html", page_context(request, title="已退订", message="您的全部关注订阅与推送已停用，可登录后重新开启。")
    )


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/settings", response_class=HTMLResponse)
def admin_spa_page(request: Request, _admin: User = Depends(admin_user)):
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse("<h1>智投内参管理后台 · Vue 3 加载中...</h1>")


@app.post("/admin/accounts/{account_id}/delete")
def delete_account(
    account_id: int,
    request: Request,
    csrf_token: str = Form(),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    account = db.get(XueqiuAccount, account_id)
    if not account:
        flash(request, "抓取源不存在", "error")
        return RedirectResponse("/admin", status_code=303)
    cancel_queued_deliveries(db, user_id=0, account_id=account.id)
    db.delete(account)
    db.commit()
    flash(request, f"已删除抓取源：{account.display_name}", "success")
    return RedirectResponse("/admin", status_code=303)



def _background_fetch_account(account_id: int) -> None:
    """请求返回后异步立即抓取，避免超时源长时间阻塞管理请求。"""
    from app.database import SessionLocal
    from app.services import fetch_account

    try:
        with SessionLocal() as task_db:
            account = task_db.get(XueqiuAccount, account_id)
            if not account:
                return
            count = fetch_account(task_db, account)
            logger.info("后台立即抓取【%s】完成，新动态 %s 条", account.display_name, count)
    except Exception as exc:
        logger.warning("后台立即抓取失败 (account_id=%s): %s", account_id, exc)


@app.post("/admin/accounts/{account_id}/resume")
def admin_resume_account(
    account_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    account = db.get(XueqiuAccount, account_id)
    if not account:
        flash(request, "抓取源不存在", "error")
        return RedirectResponse("/admin", status_code=303)
    account.failed_fetches = 0
    account.paused_until = None
    account.last_error = None
    db.commit()
    background_tasks.add_task(_background_fetch_account, account.id)
    flash(request, f"已解除暂停【{account.display_name}】，正在后台立即抓取，请稍后刷新查看结果。", "success")
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/settings")
def admin_settings_page(
    request: Request,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    from app import appconfig

    digest_hour = appconfig.get_cfg("digest_hour", settings.digest_hour)
    app_timezone = appconfig.get_cfg("app_timezone", settings.app_timezone)
    digest_max_per_email = int(appconfig.get_cfg("digest_max_per_email", settings.digest_max_per_email))
    register_answer = str(appconfig.get_cfg("register_answer", REGISTER_ANSWER))
    llm_enabled = str(appconfig.get_cfg("llm_enabled", str(settings.llm_enabled))).lower() in ("true", "1", "yes", "on")
    llm_api_base = str(appconfig.get_cfg("llm_api_base", settings.llm_api_base))
    llm_model = str(appconfig.get_cfg("llm_model", settings.llm_model))
    raw_key = appconfig.get_cfg("llm_api_key", settings.llm_api_key)
    has_llm_key = bool(raw_key)
    wechat_token_set = bool(str(appconfig.get_cfg("wechat_ingest_token", "")).strip())
    wechat_ingest_enabled = str(appconfig.get_cfg("wechat_ingest_enabled", "false")).strip().lower() in ("true", "1", "yes", "on")
    feishu_app_id = str(appconfig.get_cfg("feishu_app_id", "")).strip()
    has_feishu_secret = bool(str(appconfig.get_cfg("feishu_app_secret", "")).strip())
    llm_fallback_api_base = str(appconfig.get_cfg("llm_fallback_api_base", "")).strip()
    llm_fallback_model = str(appconfig.get_cfg("llm_fallback_model", "")).strip()
    has_llm_fallback_key = bool(str(appconfig.get_cfg("llm_fallback_api_key", "")).strip())

    return templates.TemplateResponse(
        request,
        "admin_settings.html",
        page_context(
            request,
            digest_hour=digest_hour,
            app_timezone=app_timezone,
            digest_max_per_email=digest_max_per_email,
            register_answer=register_answer,
            wechat_token_set=wechat_token_set,
            wechat_ingest_enabled=wechat_ingest_enabled,
            feishu_app_id=feishu_app_id,
            has_feishu_secret=has_feishu_secret,
            llm_fallback_api_base=llm_fallback_api_base,
            llm_fallback_model=llm_fallback_model,
            has_llm_fallback_key=has_llm_fallback_key,
            llm_enabled=llm_enabled,
            llm_api_base=llm_api_base,
            llm_model=llm_model,
            has_llm_key=has_llm_key,
        ),
    )


@app.post("/admin/settings")
def admin_settings_save(
    request: Request,
    digest_hour: int = Form(ge=0, le=23),
    app_timezone: str = Form(),
    digest_max_per_email: int = Form(default=0),
    register_answer: str = Form(""),
    wechat_ingest_token: str = Form(""),
    wechat_ingest_enabled: str | None = Form(None),
    feishu_app_id: str = Form(""),
    feishu_app_secret: str = Form(""),
    llm_enabled: str | None = Form(None),
    llm_api_base: str = Form(""),
    llm_model: str = Form(""),
    llm_api_key: str = Form(""),
    llm_fallback_api_base: str = Form(""),
    llm_fallback_model: str = Form(""),
    llm_fallback_api_key: str = Form(""),
    action: str = Form(""),
    csrf_token: str = Form(),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    from app import appconfig

    if action == "clear_wechat_token":
        appconfig.set_cfg("wechat_ingest_token", "")
        flash(request, "微信接入令牌已清除，接入接口将拒绝所有请求，直至重新配置。", "success")
        return RedirectResponse("/admin/settings", status_code=303)

    tz = app_timezone.strip()
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        flash(request, "时区无效", "error")
        return RedirectResponse("/admin/settings", status_code=303)
    appconfig.set_cfg("digest_hour", str(int(digest_hour)))
    appconfig.set_cfg("app_timezone", tz)
    appconfig.set_cfg("digest_max_per_email", str(int(digest_max_per_email)))
    if register_answer.strip():
        appconfig.set_cfg("register_answer", register_answer.strip())
    # 以下为敏感凭据：留空或回传掩码哨兵都表示保持原值不变，只有真正的新值才写入
    if is_new_secret(wechat_ingest_token):
        appconfig.set_cfg("wechat_ingest_token", wechat_ingest_token.strip())
    appconfig.set_cfg("wechat_ingest_enabled", "true" if wechat_ingest_enabled else "false")
    if feishu_app_id.strip():
        appconfig.set_cfg("feishu_app_id", feishu_app_id.strip())
    if is_new_secret(feishu_app_secret):
        appconfig.set_cfg("feishu_app_secret", encrypt_secret(feishu_app_secret.strip(), settings.secret_key))

    # 保存全局大模型设置
    appconfig.set_cfg("llm_enabled", "true" if llm_enabled else "false")
    if llm_api_base.strip():
        appconfig.set_cfg("llm_api_base", llm_api_base.strip())
    if llm_model.strip():
        appconfig.set_cfg("llm_model", llm_model.strip())
    if is_new_secret(llm_api_key):
        enc_key = encrypt_secret(llm_api_key.strip(), settings.secret_key)
        appconfig.set_cfg("llm_api_key", enc_key)
    if llm_fallback_api_base.strip():
        appconfig.set_cfg("llm_fallback_api_base", llm_fallback_api_base.strip())
    if llm_fallback_model.strip():
        appconfig.set_cfg("llm_fallback_model", llm_fallback_model.strip())
    if is_new_secret(llm_fallback_api_key):
        appconfig.set_cfg("llm_fallback_api_key", encrypt_secret(llm_fallback_api_key.strip(), settings.secret_key))

    flash(request, "系统运行与大模型设置已保存", "success")
    return RedirectResponse("/admin/settings", status_code=303)


def send_verification_email(email: str, token: str) -> None:
    url = safe_href(f"{settings.base_url.rstrip('/')}/verify/{token}")
    app_name = escape_text(settings.app_name)
    send_email(email, f"验证你的{app_name}邮箱", f'<p>管理员已为你创建账号。</p><p><a href="{url}">验证邮箱并启用账号</a></p>')


def send_password_reset_email(email: str, token: str) -> None:
    url = safe_href(f"{settings.base_url.rstrip('/')}/reset-password/{token}")
    app_name = escape_text(settings.app_name)
    send_email(
        email,
        f"重置{app_name}登录密码",
        f'<p>您（或有人）申请重置 {app_name} 的登录密码。</p>'
        f'<p><a href="{url}">点击这里设置新密码</a></p>'
        f'<p>链接 2 小时内有效。若非本人操作，请忽略本邮件。</p>',
    )


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "forgot_password.html", page_context(request))


@app.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    ident: str = Form(""),
    csrf_token: str = Form(),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    ip = client_ip(request)
    # 每次尝试都计数：防止向已知邮箱轰炸重置邮件
    if login_is_blocked(f"fp:{ip}"):
        flash(request, "如果该账号存在且绑定了邮箱，重置邮件已发送，请查收。", "info")
        return RedirectResponse("/forgot-password", status_code=303)
    record_login_fail(f"fp:{ip}")
    ident = ident.strip()
    if ident:
        user = db.scalar(select(User).where((User.email == ident.lower()) | (User.username == ident)))
        if user and user.is_enabled and user.email and "@" in user.email and not user.email.endswith("@local"):
            background_tasks.add_task(send_password_reset_email, user.email, make_token(user.id, "reset-password"))
    # 统一提示，避免枚举账号是否存在
    flash(request, "如果该账号存在且绑定了邮箱，重置邮件已发送，请查收（含垃圾箱）。", "info")
    return RedirectResponse("/forgot-password", status_code=303)


@app.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    user_id = read_token(token, "reset-password", max_age=2 * 3600)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_enabled:
        return templates.TemplateResponse(
            request, "message.html", page_context(request, title="链接无效", message="重置链接无效或已过期，请重新申请。"), status_code=400
        )
    return templates.TemplateResponse(request, "reset_password.html", page_context(request, reset_token=token))


@app.post("/reset-password/{token}")
def reset_password_submit(
    token: str,
    request: Request,
    password: str = Form(),
    confirm: str = Form(),
    csrf_token: str = Form(),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    user_id = read_token(token, "reset-password", max_age=2 * 3600)
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_enabled:
        return templates.TemplateResponse(
            request, "message.html", page_context(request, title="链接无效", message="重置链接无效或已过期，请重新申请。"), status_code=400
        )
    if len(password) < 6:
        flash(request, "密码至少 6 位", "error")
        return RedirectResponse(f"/reset-password/{token}", status_code=303)
    if password != confirm:
        flash(request, "两次输入的密码不一致", "error")
        return RedirectResponse(f"/reset-password/{token}", status_code=303)
    user.password_hash = hash_password(password)
    db.commit()
    flash(request, "密码已重置，请使用新密码登录", "success")
    return RedirectResponse("/login", status_code=303)


@app.post("/settings/password")
def change_password(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    csrf_token: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    if not verify_password(old_password, user.password_hash):
        flash(request, "当前密码不正确", "error")
        return RedirectResponse("/settings", status_code=303)
    if len(new_password) < 6:
        flash(request, "新密码至少 6 位", "error")
        return RedirectResponse("/settings", status_code=303)
    if new_password != confirm_password:
        flash(request, "两次输入的新密码不一致", "error")
        return RedirectResponse("/settings", status_code=303)
    user.password_hash = hash_password(new_password)
    db.commit()
    flash(request, "登录密码已修改", "success")
    return RedirectResponse("/settings", status_code=303)


@app.post("/admin/users")
def create_user(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(),
    password: str = Form(),
    is_admin: str | None = Form(None),
    csrf_token: str = Form(),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    try:
        normalized_email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        flash(request, "邮箱格式无效", "error")
        return RedirectResponse("/admin", status_code=303)
    if len(password) < 10:
        flash(request, "初始密码至少 10 位", "error")
        return RedirectResponse("/admin", status_code=303)
    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        is_admin=is_admin == "on",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "该邮箱已存在", "error")
        return RedirectResponse("/admin", status_code=303)
    background_tasks.add_task(send_verification_email, user.email, make_token(user.id, "verify-email"))
    flash(request, "用户已创建，验证邮件已进入发送队列", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    request: Request,
    csrf_token: str = Form(),
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        flash(request, "不能停用当前登录的管理员", "error")
    else:
        user.is_enabled = not user.is_enabled
        if not user.is_enabled:
            cancel_queued_deliveries(db, user.id)
        db.commit()
        flash(request, "用户状态已更新", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/verify-email")
def resend_verification(
    user_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    user = db.get(User, user_id)
    if not user or not user.is_enabled:
        raise HTTPException(status_code=404)
    if user.email_verified:
        flash(request, "该邮箱已经验证", "info")
    else:
        background_tasks.add_task(send_verification_email, user.email, make_token(user.id, "verify-email"))
        flash(request, "验证邮件已重新进入发送队列", "success")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/deliveries/{delivery_id}/retry")
def retry_delivery(
    delivery_id: int,
    request: Request,
    csrf_token: str = Form(),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf_token)
    delivery = db.get(Delivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404)
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.last_error = None
    # 重置双通道子状态，确保邮件与 Webhook 都会重新投递
    delivery.email_status = "pending"
    delivery.webhook_status = "pending"
    db.commit()
    flash(request, "投递已重新进入队列", "success")
    return RedirectResponse("/admin", status_code=303)


# ==============================================================================
# RESTful JSON APIs for Vue 3 SPA Frontend
# ==============================================================================

class LoginJsonRequest(BaseModel):
    email: str
    password: str

@app.get("/api/auth/me")
def api_auth_me(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_enabled:
        raise HTTPException(status_code=401, detail="请先登录")
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "is_admin": user.is_admin,
        }
    }

@app.post("/api/auth/login")
def api_auth_login(payload: LoginJsonRequest, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if login_is_blocked(ip):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 10 分钟后再试")
    ident = payload.email.strip()
    user = db.scalar(
        select(User).where(
            (User.email == ident.lower()) | (User.username == ident)
        )
    )
    if not user or not verify_password(payload.password, user.password_hash):
        record_login_fail(ip)
        raise HTTPException(status_code=400, detail="用户名/邮箱或密码错误")
    if not user.is_enabled:
        record_login_fail(ip)
        raise HTTPException(status_code=403, detail="账号已停用")
    clear_login_fails(ip)
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf"] = new_csrf_token()
    return {
        "ok": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "is_admin": user.is_admin,
        }
    }

@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


class RegisterJsonRequest(BaseModel):
    username: str
    password: str
    confirm: str
    email: str = ""
    answer: str


@app.post("/api/auth/register")
def api_auth_register(
    payload: RegisterJsonRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    if login_is_blocked(f"reg:{ip}"):
        raise HTTPException(status_code=429, detail="尝试次数过多，请 10 分钟后再试")
    username = payload.username.strip()
    email = payload.email.strip().lower()
    answer = payload.answer.strip()
    password = payload.password
    confirm = payload.confirm

    if not username:
        raise HTTPException(status_code=400, detail="请填写用户名")
    if len(username) < 3 or len(username) > 32:
        raise HTTPException(status_code=400, detail="用户名长度需为 3-32 个字符")
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if password != confirm:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    if answer != effective_register_answer():
        record_login_fail(f"reg:{ip}")
        raise HTTPException(status_code=400, detail="群名答案不正确")
    if email:
        try:
            validate_email(email, check_deliverability=False)
        except Exception:
            raise HTTPException(status_code=400, detail="邮箱格式不正确")

    if db.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(status_code=400, detail="该用户名已被注册")
    if email and db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    using_email = bool(email)
    user = User(
        username=username,
        email=email or f"{username}@local",
        password_hash=hash_password(password),
        email_verified=not using_email,
        is_enabled=True,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="用户名或邮箱冲突，请换一个")

    if using_email:
        background_tasks.add_task(
            send_verification_email, user.email, make_token(user.id, "verify-email")
        )
        return {
            "ok": True,
            "require_verify": True,
            "msg": "注册成功！验证邮件已发送到您的邮箱，请查收完成验证后登录。",
        }
    else:
        clear_login_fails(f"reg:{ip}")
        request.session.clear()
        request.session["user_id"] = user.id
        request.session["csrf"] = new_csrf_token()
        return {
            "ok": True,
            "require_verify": False,
            "msg": "注册成功！已为您自动登录。",
            "user": {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "is_admin": user.is_admin,
            },
        }

@app.get("/api/dashboard/data")
def api_dashboard_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    subscriptions = db.scalars(
        select(Subscription)
        .options(joinedload(Subscription.account))
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.desc())
    ).all()

    acct_ids = [s.account.id for s in subscriptions if s.account]
    latest_items_map = {}
    if acct_ids:
        from sqlalchemy import func
        subq = (
            select(FeedItem.xueqiu_account_id, func.max(FeedItem.id).label("max_id"))
            .where(FeedItem.xueqiu_account_id.in_(acct_ids))
            .group_by(FeedItem.xueqiu_account_id)
            .subquery()
        )
        latest_items = db.scalars(
            select(FeedItem).join(subq, FeedItem.id == subq.c.max_id)
        ).all()
        for it in latest_items:
            latest_items_map[it.xueqiu_account_id] = {
                "title": (it.title or "").strip(),
                "published_at": it.published_at.isoformat() if it.published_at else None,
            }

    sub_list = []
    for s in subscriptions:
        acct_dict = None
        if s.account:
            acct_dict = {
                "id": s.account.id,
                "platform": getattr(s.account, "platform", "xueqiu"),
                "name": s.account.display_name,
                "display_name": s.account.display_name,
                "xueqiu_user_id": s.account.xueqiu_user_id,
                "feed_type": getattr(s.account, "feed_type", "10"),
                "failed_fetches": getattr(s.account, "failed_fetches", 0),
                "paused_until": s.account.paused_until.isoformat() if s.account.paused_until else None,
                "last_checked_at": s.account.last_checked_at.isoformat() if s.account.last_checked_at else None,
                "latest_post": latest_items_map.get(s.account.id),
            }
        sub_list.append({
            "id": s.id,
            "user_id": s.user_id,
            "xueqiu_account_id": s.xueqiu_account_id,
            "is_enabled": bool(s.is_enabled),
            "delivery_mode": s.delivery_mode or "immediate",
            "delivery_channel": s.delivery_channel or "both",
            "ai_summary_enabled": bool(s.ai_summary_enabled),
            "keywords_include": s.keywords_include or "",
            "keywords_exclude": s.keywords_exclude or "",
            "alert_keywords": s.alert_keywords or "",
            "filter_min_length": s.filter_min_length or 0,
            "account": acct_dict,
        })
    from app import appconfig
    # 展示当前用户自己的汇总发送时刻（未自选时回退全局默认）
    digest_hour = appconfig.digest_hour_for_user(user)
    return {
        "subscriptions": sub_list,
        "digest_hour": digest_hour,
    }

@app.get("/api/dashboard/timeline")
def api_dashboard_timeline(limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    acct_ids = db.scalars(select(Subscription.xueqiu_account_id).where(Subscription.user_id == user.id)).all()
    if not acct_ids:
        return {"items": []}
    
    items = db.scalars(
        select(FeedItem)
        .options(joinedload(FeedItem.account))
        .where(FeedItem.xueqiu_account_id.in_(acct_ids))
        .order_by(FeedItem.published_at.desc())
        .limit(limit)
    ).all()

    from app.services.fetcher import to_local_datetime_str
    res = []
    for item in items:
        res.append({
            "id": item.id,
            "title": item.title,
            "content": item.content,
            "link": item.link,
            "author_name": item.account.display_name if item.account else "未知大V",
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "published_at_str": to_local_datetime_str(item.published_at),
        })
    return {"items": res}

class AddSubJsonRequest(BaseModel):
    platform: str = "xueqiu"
    user_id: str
    name: str = ""
    feed_type: str = "10"
    delivery_mode: str = "immediate"
    delivery_channel: str = "both"
    ai_summary_enabled: bool = True

@app.post("/api/subscriptions")
def api_add_subscription(payload: AddSubJsonRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # 平台白名单 / custom_rss 管理员闸门 / 微信接入开关 / 源标识合法性：统一由 check_subscription_source 校验
    checked = check_subscription_source(
        platform=payload.platform,
        source_key=payload.user_id,
        display_name=payload.name.strip() or payload.user_id.strip(),
        is_admin=user.is_admin,
    )
    if not checked.ok:
        raise HTTPException(status_code=checked.status_code, detail=checked.message)
    platform = checked.platform
    uid = checked.source_key
    display_name = checked.display_name

    feed_type = payload.feed_type.strip().lower() or "10"

    acct = db.scalar(select(XueqiuAccount).where(
        XueqiuAccount.platform == platform,
        XueqiuAccount.xueqiu_user_id == uid,
    ))
    if not acct:
        acct = XueqiuAccount(
            platform=platform,
            xueqiu_user_id=uid,
            display_name=display_name,
            feed_type=feed_type,
        )
        db.add(acct)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            acct = db.scalar(select(XueqiuAccount).where(
                XueqiuAccount.platform == platform,
                XueqiuAccount.xueqiu_user_id == uid,
            ))
    else:
        if display_name and display_name != uid:
            acct.display_name = display_name
    
    del_mode = payload.delivery_mode if payload.delivery_mode in {"immediate", "daily"} else "immediate"
    del_chan = payload.delivery_channel if payload.delivery_channel in {"both", "webhook", "email"} else "both"

    existing = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.xueqiu_account_id == acct.id))
    if not existing:
        sub = Subscription(
            user_id=user.id,
            xueqiu_account_id=acct.id,
            is_enabled=True,
            delivery_mode=del_mode,
            delivery_channel=del_chan,
            ai_summary_enabled=payload.ai_summary_enabled,
        )
        db.add(sub)
        db.commit()
    else:
        existing.is_enabled = True
        existing.delivery_mode = del_mode
        existing.delivery_channel = del_chan
        existing.ai_summary_enabled = payload.ai_summary_enabled
        db.commit()
    return {"ok": True, "account_id": acct.id}

class UpdateSubJsonRequest(BaseModel):
    is_enabled: bool | None = None
    delivery_mode: str | None = None
    delivery_channel: str | None = None
    ai_summary_enabled: bool | None = None

@app.patch("/api/subscriptions/{subscription_id}")
def api_update_subscription(
    subscription_id: int,
    payload: UpdateSubJsonRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到该订阅")
    if payload.is_enabled is not None:
        sub.is_enabled = payload.is_enabled
    if payload.delivery_mode is not None:
        if payload.delivery_mode in {"immediate", "daily"}:
            sub.delivery_mode = payload.delivery_mode
    if payload.delivery_channel is not None:
        if payload.delivery_channel in {"both", "webhook", "email"}:
            sub.delivery_channel = payload.delivery_channel
    if payload.ai_summary_enabled is not None:
        sub.ai_summary_enabled = payload.ai_summary_enabled
    db.commit()
    return {"ok": True}

@app.delete("/api/subscriptions/{subscription_id}")
def api_delete_subscription(subscription_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到该订阅")
    db.delete(sub)
    db.commit()
    return {"ok": True}

@app.post("/api/subscriptions/{subscription_id}/trigger")
def api_trigger_subscription(subscription_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.user_id != user.id or not sub.account:
        raise HTTPException(status_code=404, detail="未找到该订阅")
    from app.services.fetcher import fetch_account
    try:
        count = fetch_account(db, sub.account)
        return {"ok": True, "new_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/user/settings")
def api_get_user_settings(user: User = Depends(current_user)):
    from app import appconfig
    # 该用户自己的 digest_hour 列（未自选时显示全局默认），不再读写 app_config
    digest_hour = appconfig.digest_hour_for_user(user)
    # 敏感字段只回显固定掩码 + has_xxx 标记：这里不再调用 decrypt_secret，
    # 明文不会进入响应体、页面 DOM 与前端堆，XSS 也只能读到哨兵字符串
    return {
        "llm_enabled": bool(user.llm_enabled) if user.llm_enabled is not None else True,
        "llm_api_base": user.llm_api_base or "",
        "llm_api_key": mask_secret(user.llm_api_key),
        "has_llm_api_key": bool(user.llm_api_key),
        "llm_model": user.llm_model or "",
        "digest_hour": digest_hour,
        "smtp_host": user.smtp_host or "",
        "smtp_port": user.smtp_port or 465,
        "smtp_username": user.smtp_username or "",
        "smtp_password": mask_secret(user.smtp_password),
        "has_smtp_password": bool(user.smtp_password),
        "smtp_from": user.smtp_from or "",
        "smtp_ssl": bool(user.smtp_ssl) if user.smtp_ssl is not None else True,
        "smtp_starttls": bool(user.smtp_starttls) if user.smtp_starttls is not None else False,
        "webhook_enabled": bool(user.webhook_enabled),
        "webhook_type": user.webhook_type or "auto",
        # webhook_url 内含 access_token，与其余凭据同等处理：只回显哨兵，不给尾号提示
        "webhook_url": mask_secret(user.webhook_url),
        "has_webhook_url": bool(user.webhook_url),
        "webhook_secret": mask_secret(user.webhook_secret),
        "has_webhook_secret": bool(user.webhook_secret),
    }

class UpdateSettingsJsonRequest(BaseModel):
    llm_enabled: bool = True
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # 仅作用于当前用户自己的 digest_hour 列，必须是合法整点
    digest_hour: int = Field(default=20, ge=0, le=23)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_ssl: bool = True
    smtp_starttls: bool = False
    webhook_enabled: bool = False
    webhook_type: str = "auto"
    webhook_url: str = ""
    webhook_secret: str = ""

@app.post("/api/user/settings")
def api_save_user_settings(payload: UpdateSettingsJsonRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.security import encrypt_secret
    user.llm_enabled = payload.llm_enabled
    user.llm_api_base = payload.llm_api_base.strip() or None
    user.llm_model = payload.llm_model.strip() or None
    if is_new_secret(payload.llm_api_key):
        user.llm_api_key = encrypt_secret(payload.llm_api_key.strip(), settings.secret_key)
    elif payload.llm_api_key == "":
        user.llm_api_key = None
    
    user.smtp_host = payload.smtp_host.strip() or None
    user.smtp_port = payload.smtp_port
    user.smtp_username = payload.smtp_username.strip() or None
    if is_new_secret(payload.smtp_password):
        user.smtp_password = encrypt_secret(payload.smtp_password.strip(), settings.secret_key)
    elif payload.smtp_password == "":
        user.smtp_password = None
    user.smtp_from = payload.smtp_from.strip() or None
    user.smtp_ssl = payload.smtp_ssl
    user.smtp_starttls = payload.smtp_starttls

    user.webhook_enabled = payload.webhook_enabled
    user.webhook_type = payload.webhook_type.strip() or "auto"
    if is_new_secret(payload.webhook_url):
        user.webhook_url = encrypt_secret(payload.webhook_url.strip(), settings.secret_key)
    elif payload.webhook_url == "":
        user.webhook_url = None
    if is_new_secret(payload.webhook_secret):
        user.webhook_secret = encrypt_secret(payload.webhook_secret.strip(), settings.secret_key)
    elif payload.webhook_secret == "":
        user.webhook_secret = None

    # 每日汇总发送整点写用户自己的列，不再改全局 app_config（越界值已在请求模型处拦截）
    user.digest_hour = payload.digest_hour
    db.commit()
    return {"ok": True}

@app.post("/api/user/settings/test-webhook")
def api_test_webhook(user: User = Depends(current_user)):
    from app.webhook import send_user_webhook
    ok = send_user_webhook(
        user,
        "群机器人 Webhook 测试消息",
        "🎉 恭喜！群机器人 Webhook 通道测试成功！\n\n系统已成功打通群通知推送，后续博主动态与每日 AI 投研内参将自动同步到本群。",
        link=settings.base_url,
    )
    if ok:
        return {"ok": True, "message": "测试消息已成功发送至群聊！"}
    else:
        raise HTTPException(status_code=400, detail="群机器人测试发送失败，请检查 Webhook 链接或密钥是否正确")

@app.post("/api/user/settings/test-email")
def api_test_email(user: User = Depends(current_user)):
    from app.mailer import send_email, user_smtp_config
    try:
        cfg = user_smtp_config(user)
        send_email(
            to=user.email,
            subject="【雪球订阅助手】SMTP 邮件发信通道测试",
            html="<p>恭喜！您的专属邮件发信通道配置正确，测试邮件投递成功！</p><p>智投内参 · 全网财经舆情监控与 AI 智能投研平台</p>",
            config=cfg,
        )
        return {"ok": True, "message": f"测试邮件已成功发送至 {user.email}，请查收！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"发信失败：{str(e)}")

class ChangePasswordJsonRequest(BaseModel):
    old_password: str
    new_password: str

@app.post("/api/user/password")
def api_change_password(payload: ChangePasswordJsonRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True, "message": "登录密码已成功修改"}


# ==========================================
# Vue 3 Admin Management RESTful APIs
# ==========================================

@app.get("/api/admin/data")
def api_admin_data(
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    users = db.scalars(
        select(User)
        .options(joinedload(User.subscriptions))
        .order_by(User.created_at.desc())
    ).unique().all()
    user_list = []
    for u in users:
        user_list.append({
            "id": u.id,
            "email": u.email,
            "username": u.username,
            "is_admin": u.is_admin,
            "is_enabled": u.is_enabled,
            "email_verified": u.email_verified,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "subscriptions_count": len(u.subscriptions),
        })

    accounts = db.scalars(
        select(XueqiuAccount)
        .options(joinedload(XueqiuAccount.subscriptions))
        .order_by(XueqiuAccount.id.desc())
    ).unique().all()

    ranked_items = select(
        FeedItem.id.label("feed_item_id"),
        func.row_number().over(
            partition_by=FeedItem.xueqiu_account_id,
            order_by=(FeedItem.published_at.desc(), FeedItem.created_at.desc(), FeedItem.id.desc()),
        ).label("item_rank"),
    ).subquery()
    latest_items = db.scalars(
        select(FeedItem)
        .join(ranked_items, FeedItem.id == ranked_items.c.feed_item_id)
        .where(ranked_items.c.item_rank == 1)
    ).all()
    latest_items_by_account = {item.xueqiu_account_id: item for item in latest_items}

    account_list = []
    from app.services.fetcher import to_local_datetime_str
    for a in accounts:
        li = latest_items_by_account.get(a.id)
        account_list.append({
            "id": a.id,
            "platform": a.platform,
            "xueqiu_user_id": a.xueqiu_user_id,
            "display_name": a.display_name,
            "feed_type": a.feed_type,
            "failed_fetches": a.failed_fetches,
            "paused_until": a.paused_until.isoformat() if a.paused_until else None,
            "last_checked_at": a.last_checked_at.isoformat() if a.last_checked_at else None,
            "last_error": a.last_error,
            "latest_item_title": li.title if li else None,
            "latest_item_time": to_local_datetime_str(li.published_at) if (li and li.published_at) else None,
            "subscriptions_count": len(a.subscriptions),
        })

    failed_deliveries = db.scalars(
        select(Delivery)
        .options(joinedload(Delivery.user), joinedload(Delivery.feed_item))
        .where(Delivery.status == "failed")
        .order_by(Delivery.id.desc())
        .limit(100)
    ).all()
    failed_list = []
    for d in failed_deliveries:
        # 注意：Delivery 模型没有 error_message / retry_count / created_at / channel 列，
        # 直接读取会抛 AttributeError 使整个接口 500。下面只使用真实存在的列。
        failed_list.append({
            "id": d.id,
            "user_id": d.user_id,
            "user_email": d.user.email if d.user else "未知用户",
            "title": d.feed_item.title if d.feed_item else "无标题",
            # 前端 (Admin.vue 与已构建的 bundle) 读取这两个键名，保持不变，仅改取值来源
            "error_message": d.last_error or "",
            "retry_count": d.attempts,
            # Delivery 无创建时间列；失败行的 sent_at 恒为 NULL，如实暴露下次重试时间
            "next_attempt_at": d.next_attempt_at.isoformat() if d.next_attempt_at else None,
            # 通道不虚构：如实暴露投递频次与双通道子状态
            "delivery_mode": d.delivery_mode,
            "email_status": d.email_status,
            "webhook_status": d.webhook_status,
        })

    stats = {
        "users": db.scalar(select(func.count()).select_from(User)),
        "subscriptions": db.scalar(select(func.count()).select_from(Subscription).where(Subscription.is_enabled.is_(True))),
        "failed": db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "failed")),
    }

    from app import appconfig
    runtime_settings = {
        "digest_hour": appconfig.global_digest_hour(),
        "app_timezone": str(appconfig.get_cfg("app_timezone", settings.app_timezone)),
        "digest_max_per_email": int(appconfig.get_cfg("digest_max_per_email", settings.digest_max_per_email)),
        "wechat_ingest_enabled": str(appconfig.get_cfg("wechat_ingest_enabled", "false")).strip().lower() in ("true", "1", "yes", "on"),
        "llm_enabled": str(appconfig.get_cfg("llm_enabled", str(settings.llm_enabled))).lower() in ("true", "1", "yes", "on"),
        "llm_api_base": str(appconfig.get_cfg("llm_api_base", settings.llm_api_base)),
        "llm_model": str(appconfig.get_cfg("llm_model", settings.llm_model)),
        "has_llm_key": bool(appconfig.get_cfg("llm_api_key", settings.llm_api_key)),
    }

    return {
        "stats": stats,
        "users": user_list,
        "accounts": account_list,
        "failed_deliveries": failed_list,
        "settings": runtime_settings,
    }

class AdminCreateUserRequest(BaseModel):
    email: str
    password: str
    is_admin: bool = False

@app.post("/api/admin/users")
def api_admin_create_user(
    payload: AdminCreateUserRequest,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="邮箱不能为空")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=400, detail="该邮箱已注册")
    new_u = User(
        email=email,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        is_enabled=True,
        email_verified=True,
    )
    db.add(new_u)
    db.commit()
    return {"ok": True, "user_id": new_u.id}

@app.post("/api/admin/users/{user_id}/toggle")
def api_admin_toggle_user(
    user_id: int,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == _admin.id:
        raise HTTPException(status_code=400, detail="不能禁用当前登录的自身账号")
    target.is_enabled = not target.is_enabled
    db.commit()
    return {"ok": True, "is_enabled": target.is_enabled}

@app.post("/api/admin/users/{user_id}/verify-email")
def api_admin_verify_user_email(
    user_id: int,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.email_verified = True
    db.commit()
    return {"ok": True}

@app.post("/api/admin/accounts/{account_id}/resume")
def api_admin_resume_account(
    account_id: int,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    acc = db.get(XueqiuAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="抓取源不存在")
    acc.failed_fetches = 0
    acc.paused_until = None
    db.commit()
    from app.services.fetcher import fetch_account
    try:
        fetch_account(db, acc)
    except Exception:
        pass
    return {"ok": True, "message": f"已解除暂停【{acc.display_name}】并触发抓取"}

@app.delete("/api/admin/accounts/{account_id}")
def api_admin_delete_account(
    account_id: int,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    acc = db.get(XueqiuAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="抓取源不存在")
    db.delete(acc)
    db.commit()
    return {"ok": True}

@app.post("/api/admin/deliveries/{delivery_id}/retry")
def api_admin_retry_delivery(
    delivery_id: int,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    delivery = db.get(Delivery, delivery_id)
    if not delivery or delivery.status != "failed":
        raise HTTPException(status_code=404, detail="未找到该失败记录")
    delivery.status = "pending"
    # Delivery.error_message 并不存在：赋值不会持久化，导致重发后仍显示上一次的旧错误。
    # 改为清空真实列 last_error。
    delivery.last_error = None
    db.commit()
    from app.services.notifier import send_immediate_deliveries
    try:
        send_immediate_deliveries(db)
    except Exception:
        pass
    return {"ok": True, "message": "已重置状态并触发立即投递！"}

class AdminSaveSettingsRequest(BaseModel):
    # 全局默认汇总小时：仅对未自选 digest_hour 的用户生效，越界值一律拒绝
    digest_hour: int = Field(ge=0, le=23)
    app_timezone: str
    digest_max_per_email: int = 0
    wechat_ingest_enabled: bool = False
    llm_enabled: bool = True
    llm_api_base: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

@app.post("/api/admin/settings")
def api_admin_save_settings(
    payload: AdminSaveSettingsRequest,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    from app import appconfig
    appconfig.set_cfg("digest_hour", str(payload.digest_hour))
    appconfig.set_cfg("app_timezone", payload.app_timezone)
    appconfig.set_cfg("digest_max_per_email", str(payload.digest_max_per_email))
    appconfig.set_cfg("wechat_ingest_enabled", "true" if payload.wechat_ingest_enabled else "false")
    appconfig.set_cfg("llm_enabled", "true" if payload.llm_enabled else "false")
    if payload.llm_api_base.strip():
        appconfig.set_cfg("llm_api_base", payload.llm_api_base.strip())
    if payload.llm_model.strip():
        appconfig.set_cfg("llm_model", payload.llm_model.strip())
    # 哨兵 = 保持原全局 Key 不变；写入时与后台 Jinja 保存一致做加密落盘
    # （decrypt_secret 对历史明文原样放行，兼容旧数据）
    if is_new_secret(payload.llm_api_key):
        appconfig.set_cfg("llm_api_key", encrypt_secret(payload.llm_api_key.strip(), settings.secret_key))
    return {"ok": True, "message": "全局运行设置已保存！"}

@app.get("/api/digests/history")
def api_digests_history(limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.models import AiSummaryCache
    caches = db.scalars(
        select(AiSummaryCache)
        .order_by(AiSummaryCache.id.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": c.id,
                "summary_text": c.summary_text or "",
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in caches
        ]
    }
