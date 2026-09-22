import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select

from app.config import get_settings
from app.database import Base, SessionLocal, engine, run_migrations
from app.models import DigestLog, Subscription, XueqiuAccount
from app.services import (
    alert_missing_digest,
    fetch_account,
    notify_admin_if_failures,
    purge_old_data,
    send_daily_digests,
    send_immediate_deliveries,
    send_weekly_digests,
)
from app import appconfig


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


def effective_digest_hour() -> int:
    """全局默认汇总小时（管理员后台可改）：仅用于运维告警与周报的门控，
    每日汇总已改为按每个用户自己的 users.digest_hour 触发。"""
    return appconfig.global_digest_hour()


def effective_app_timezone() -> str:
    return appconfig.get_cfg("app_timezone", settings.app_timezone)


def run_cycle(cycle_index: int):
    # 注意：SQLite 读出的 DateTime(timezone=True) 为 naive UTC，故用 naive UTC 比较
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        accounts = db.scalars(
            select(XueqiuAccount).where(
                exists().where(
                    Subscription.xueqiu_account_id == XueqiuAccount.id,
                    Subscription.is_enabled.is_(True),
                )
            )
        ).all()
        for idx, account in enumerate(accounts):
            if getattr(account, 'platform', None) == "wechat":
                continue
            # A) 失败保护：暂停期内的源直接跳过，避免持续刷失败请求
            if account.paused_until and account.paused_until > now:
                logger.info("跳过 %s：处于暂停期（至 %s）", account.display_name, account.paused_until)
                continue
            try:
                count = fetch_account(db, account)
                logger.info("已抓取 %s，新动态 %s 条", account.display_name, count)
            except Exception:
                logger.exception("抓取 %s 失败", account.display_name)
            # 错峰抓取：账号之间加间隔+抖动，避免同一时刻拉起多个浏览器实例压垮 RSSHub
            if idx < len(accounts) - 1 and settings.fetch_account_gap_seconds > 0:
                time.sleep(settings.fetch_account_gap_seconds + random.uniform(0, 5))

    with SessionLocal() as db:
        send_immediate_deliveries(db)

    local_now = datetime.now(ZoneInfo(effective_app_timezone()))
    # 每日汇总：每轮都调用，由 send_daily_digests 内部按每个用户自己的 digest_hour 判定到点没到点
    # （未到的用户直接跳过，不产生额外查询），DigestLog(user_id, digest_date) 继续负责当天去重
    with SessionLocal() as db:
        send_daily_digests(db, local_now.date(), local_hour=local_now.hour)
    if local_now.hour >= effective_digest_hour():
        # 每日一次的失败投递运维告警（内部按日期去重）
        with SessionLocal() as db:
            notify_admin_if_failures(db)
        # 周报：每周日按全局默认 digest_hour 后执行一次（全局按周去重，不受个人偏好影响）
        if local_now.weekday() == 6:
            with SessionLocal() as db:
                send_weekly_digests(db, local_now)
    return local_now.date()


def catch_up_yesterday_digest() -> None:
    """启动追补：昨日（本地时区）从未生成过任何汇总记录时补跑昨日汇总，
    覆盖宕机错过 digest_hour 的场景；正常运行过的实例会因 DigestLog 已存在而跳过。"""
    try:
        local_now = datetime.now(ZoneInfo(effective_app_timezone()))
        yesterday = local_now.date() - timedelta(days=1)
        with SessionLocal() as db:
            has_any = db.scalar(select(DigestLog.id).where(DigestLog.digest_date == yesterday)) is not None
            if has_any:
                return
            logger.warning("检测到昨日(%s)无任何汇总记录，执行启动追补", yesterday)
            # 追补不传 local_hour：漏发的是昨天整天，与个人 digest_hour 到没到点无关
            send_daily_digests(db, yesterday)
    except Exception:
        logger.exception("昨日汇总追补失败")


def _worker_heartbeat_path() -> str:
    """心跳文件路径：与数据库同目录（web/worker 共享的数据卷内）。"""
    raw = settings.database_url.split("sqlite:///", 1)[-1]
    return os.path.join(os.path.dirname(os.path.abspath(raw)), ".worker_heartbeat")


def write_heartbeat() -> None:
    """每轮循环结束写心跳，供容器 healthcheck 判断轮询循环是否存活。"""
    try:
        path = _worker_heartbeat_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        logger.warning("写入 worker 心跳文件失败", exc_info=True)


def main() -> None:
    Base.metadata.create_all(engine)
    run_migrations()
    cycle_index = 0
    last_seen_date = None
    logger.info("worker 已启动，轮询间隔 %s 秒", settings.poll_interval_seconds)
    # 启动追补：若昨日从未生成过任何汇总（如宕机错过 digest_hour），补跑一次
    catch_up_yesterday_digest()
    while True:
        started = time.monotonic()
        try:
            run_cycle(cycle_index)
            # F) 定期清理：每 purge_every_cycles 个周期执行一次数据保留清理
            if cycle_index % settings.purge_every_cycles == 0:
                with SessionLocal() as db:
                    removed = purge_old_data(db)
                if removed:
                    logger.info("数据清理完成：%s", removed)
        except Exception:
            logger.exception("worker 周期执行失败")
        cycle_index += 1
        # 日期翻转：补跑昨日汇总（晚间 AI 失败导致 DigestLog 缺失时跨天重试），仍失败则告警
        try:
            local_now = datetime.now(ZoneInfo(effective_app_timezone()))
            if last_seen_date is not None and local_now.date() > last_seen_date:
                logger.info("检测到日期翻转（%s -> %s），执行昨日汇总补跑", last_seen_date, local_now.date())
                catch_up_yesterday_digest()
                yesterday = local_now.date() - timedelta(days=1)
                with SessionLocal() as db:
                    has_yesterday = db.scalar(
                        select(DigestLog.id).where(DigestLog.digest_date == yesterday)
                    ) is not None
                    if not has_yesterday:
                        alert_missing_digest(db, yesterday)
            last_seen_date = local_now.date()
        except Exception:
            logger.exception("日期翻转补跑失败")
        write_heartbeat()
        elapsed = time.monotonic() - started
        time.sleep(max(1, settings.poll_interval_seconds - elapsed))


if __name__ == "__main__":
    main()
