from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "智投内参"
    secret_key: str = Field(min_length=16)
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./xueqiu.db"

    admin_email: str | None = None
    admin_password: str | None = None

    rsshub_base_url: str = "http://rsshub:1200"
    rsshub_xueqiu_path: str = "/xueqiu/user/{user_id}"
    poll_interval_seconds: int = 600
    http_timeout_seconds: int = 30

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "智投内参 <notice@example.com>"
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    delivery_max_attempts: int = 5
    retry_base_seconds: int = 300
    digest_hour: int = Field(default=8, ge=0, le=23)
    app_timezone: str = "Asia/Shanghai"

    # RSS 抓取优化：RSSHub 路由支持 ?limit= 限制返回条数，减少带宽与查重开销
    fetch_limit: int = 50
    # RSS 抓取响应硬上限，避免异常源占满 worker 内存
    fetch_max_response_bytes: int = 5 * 1024 * 1024
    fetch_max_entries: int = 200
    # 自定义 RSS 只允许 HTTPS；管理员可显式关闭 HTTPS 要求用于内网自建源
    custom_rss_require_https: bool = True
    # 错峰抓取：账号之间间隔秒数（另有 0-5 秒随机抖动），避免同时拉起多个浏览器实例压垮 RSSHub
    fetch_account_gap_seconds: int = 10
    # 每日汇总单封邮件最多包含的条数，超出则拆成多封，避免超大邮件被拒
    digest_max_per_email: int = 50

    # A) 抓取失败保护：连续失败达到阈值后暂停该源，避免雪球/RSSHub 限流时刷请求连坐其他源
    max_consecutive_fetch_errors: int = 5
    fetch_error_backoff_seconds: int = 1800  # 首次暂停 30 分钟，指数退避封顶 6 小时
    fetch_error_backoff_max_seconds: int = 21600
    # F) 数据保留：定期清理，避免 SQLite 无限膨胀
    feed_item_retention_days: int = 90
    delivery_retention_days: int = 7
    # 数据清理执行频率（周期数，1 周期 = poll_interval_seconds）
    purge_every_cycles: int = 144

    # B) immediate 投递合并：同一用户的多条即时动态合并为一封邮件，避免短时连发被判垃圾
    immediate_merge_per_user: bool = True
    # 两封 immediate 邮件之间的最小间隔（秒），0 表示不限制
    immediate_send_interval_seconds: int = 0
    # C) 抓取异常守卫：非首次抓取时若新条目数超过阈值，疑似 RSSHub 改版刷屏，暂停生成投递并告警
    max_new_items_per_fetch: int = 100



    # G) 大模型 AI 每日总结配置
    llm_enabled: bool = True
    llm_api_base: str = "https://aiapi.blueswords.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-5.6-luna"
    llm_timeout_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
