import smtplib
import time
import logging
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from app.config import get_settings
from app.security import decrypt_secret

logger = logging.getLogger("app.mailer")


class SmtpConfig:
    """一组发信配置，可由全局 settings 或单个用户构造。"""
    def __init__(self, host, port, username, password, frm, ssl, starttls):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.frm = frm
        self.ssl = ssl
        self.starttls = starttls


def _global_config() -> SmtpConfig:
    s = get_settings()
    return SmtpConfig(s.smtp_host, s.smtp_port, s.smtp_username,
                       # 全局密码来自环境变量（明文），decrypt_secret 对非 enc: 值原样放行，兼容旧数据
                       s.smtp_password, s.smtp_from, s.smtp_ssl, s.smtp_starttls)


def send_email(to: str, subject: str, html: str, config: SmtpConfig | None = None,
               max_retries: int = 3, retry_wait: float = 5.0) -> None:
    """发送 HTML 邮件。

    config 为 None 时使用全局 settings；传入用户级 SmtpConfig 则用该用户自己的邮箱发信。
    发送失败（如 QQ 邮箱偶发 SSL 握手超时）会自动重试。
    """
    cfg = config or _global_config()
    if not cfg.host:
        raise RuntimeError("SMTP_HOST 未配置")

    message = EmailMessage()
    message["From"] = cfg.frm or to
    message["To"] = to
    message["Subject"] = subject
    message.set_content("请使用支持 HTML 的邮件客户端查看此邮件。")
    message.add_alternative(html, subtype="html")

    # 提取合法的信封发件人邮箱（纯 email 格式）
    smtp_from_addr = None
    if cfg.frm:
        _, parsed_addr = parseaddr(cfg.frm)
        if parsed_addr and "@" in parsed_addr:
            smtp_from_addr = parsed_addr
    if not smtp_from_addr:
        smtp_from_addr = cfg.username or to

    smtp_class = smtplib.SMTP_SSL if cfg.ssl else smtplib.SMTP
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            with smtp_class(cfg.host, cfg.port, timeout=30) as smtp:
                if cfg.starttls and not cfg.ssl:
                    smtp.starttls()
                if cfg.username:
                    smtp.login(cfg.username, cfg.password or "")
                smtp.send_message(message, from_addr=smtp_from_addr)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(retry_wait * attempt)
    raise last_exc if last_exc else RuntimeError("send_email 未知失败")


def user_smtp_config(user) -> SmtpConfig | None:
    """根据用户自管 SMTP 字段构造配置；缺失关键项时返回 None（调用方回退全局）。"""
    if not user or not user.smtp_host or not user.smtp_username:
        return None
    
    frm_addr = user.smtp_username or user.email
    if user.smtp_from and user.smtp_from.strip():
        raw_from = user.smtp_from.strip()
        name, addr = parseaddr(raw_from)
        if addr and "@" in addr:
            if name:
                frm = formataddr((name, addr))
            else:
                frm = addr
        else:
            # 纯昵称/显示名（如 "陆书蓉"）或无有效 @ 邮箱时，拼接发件人邮箱
            display_name = name or raw_from
            frm = formataddr((display_name, frm_addr))
    else:
        frm = frm_addr

    return SmtpConfig(
        host=user.smtp_host,
        port=user.smtp_port or 465,
        username=user.smtp_username,
        # 用户密码已加密落盘；旧明文数据由 decrypt_secret 原样放行，平滑兼容
        password=decrypt_secret(user.smtp_password or ""),
        frm=frm,
        ssl=user.smtp_ssl if user.smtp_ssl is not None else True,
        starttls=user.smtp_starttls if user.smtp_starttls is not None else False,
    )
