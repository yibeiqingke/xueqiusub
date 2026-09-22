import logging
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
# timeout：SQLite 写锁等待上限（秒）。web 与 worker 双进程共用同一库文件，
# 默认 5 秒在投递/汇总并发写时容易触发 "database is locked"，放宽到 30 秒
connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

logger = logging.getLogger("app.database")


def get_db():
    with SessionLocal() as db:
        yield db


def _sqlite_db_path() -> Path:
    raw = settings.database_url.split("sqlite:///", 1)[-1]
    return Path(raw).absolute()


def _default_literal(column) -> str:
    """把 SQLAlchemy 列的 Python 标量默认值转成 DDL 的 DEFAULT 子句（仅限常量）。"""
    d = column.default
    if d is None or not getattr(d, "is_scalar", False) or d.arg is None:
        return ""
    if isinstance(d.arg, bool):
        return f" DEFAULT {int(d.arg)}"
    if isinstance(d.arg, (int, float)):
        return f" DEFAULT {d.arg}"
    if isinstance(d.arg, str):
        return " DEFAULT '" + str(d.arg).replace("'", "''") + "'"
    return ""


def _backup_sqlite() -> None:
    """迁移前用 SQLite backup API 做一致性备份（WAL 安全），失败则中止迁移。"""
    import sqlite3

    src_path = _sqlite_db_path()
    if not src_path.exists():
        return
    dst = src_path.with_name(f"{src_path.name}.bak_{datetime.now():%Y%m%d_%H%M%S}")
    src = sqlite3.connect(str(src_path))
    dst_conn = sqlite3.connect(str(dst))
    try:
        with dst_conn:
            src.backup(dst_conn)
    except Exception:
        logger.exception("迁移前数据库备份失败，跳过本次迁移")
        raise
    finally:
        src.close()
        dst_conn.close()
    logger.info("已创建迁移前数据库备份：%s", dst)


def _accounts_need_rebuild(inspector) -> bool:
    """旧库 xueqiu_accounts 的唯一约束是 (xueqiu_user_id, feed_type)，缺 platform 列时需重建表。"""
    if not inspector.has_table("xueqiu_accounts"):
        return False
    with engine.connect() as conn:
        index_rows = conn.exec_driver_sql("PRAGMA index_list(xueqiu_accounts)").mappings().all()
        for row in index_rows:
            if not row["unique"] or row["origin"] != "u":
                continue
            cols = [
                r["name"]
                for r in conn.exec_driver_sql(f"PRAGMA index_info({row['name']})").mappings().all()
            ]
            if set(cols) == {"xueqiu_user_id", "feed_type"}:
                return True
    return False


def _rebuild_xueqiu_accounts() -> None:
    """按当前模型重建 xueqiu_accounts（唯一约束加入 platform），保留全部数据。"""
    from app.models import XueqiuAccount

    table = XueqiuAccount.__table__
    col_defs = []
    for column in table.columns:
        d = f"{column.name} {column.type.compile(engine.dialect)}"
        if column.primary_key:
            d += " PRIMARY KEY"
        elif not column.nullable:
            d += " NOT NULL"
        d += _default_literal(column)
        col_defs.append(d)
    col_names = ", ".join(c.name for c in table.columns)
    # platform 列在旧数据中可能为 NULL，统一回填默认值以满足新约束的 NOT NULL 语义
    select_cols = ", ".join(
        f"COALESCE({c.name}, 'xueqiu')" if c.name == "platform" else c.name
        for c in table.columns
    )
    ddl = (
        f"CREATE TABLE xueqiu_accounts_new ({', '.join(col_defs)}, "
        f"UNIQUE (platform, xueqiu_user_id, feed_type))"
    )
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            conn.exec_driver_sql("BEGIN")
            conn.exec_driver_sql(ddl)
            conn.exec_driver_sql(
                f"INSERT INTO xueqiu_accounts_new ({col_names}) SELECT {select_cols} FROM xueqiu_accounts"
            )
            conn.exec_driver_sql("DROP TABLE xueqiu_accounts")
            conn.exec_driver_sql("ALTER TABLE xueqiu_accounts_new RENAME TO xueqiu_accounts")
            conn.exec_driver_sql("COMMIT")
            logger.info("xueqiu_accounts 已按新唯一约束 (platform, xueqiu_user_id, feed_type) 重建")
        except Exception:
            conn.exec_driver_sql("ROLLBACK")
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def run_migrations() -> None:
    """SQLite 轻量启动迁移：create_all 只能建缺失的表，本函数负责
    1) 为已有表补齐模型新增的列；2) 修正旧版 xueqiu_accounts 的唯一约束。
    有任何待执行变更时先做一致性备份。web 与 worker 同时启动时通过文件锁串行化。"""
    if not settings.database_url.startswith("sqlite"):
        return
    try:
        import fcntl
    except ImportError:  # 非 POSIX 平台（本地开发）直接执行，不做跨进程互斥
        _run_migrations_locked()
        return
    lock_path = _sqlite_db_path().with_name(".migrate.lock")
    with open(lock_path, "w") as fh:
        deadline = datetime.now().timestamp() + 120
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if datetime.now().timestamp() >= deadline:
                    raise RuntimeError("等待数据库迁移锁超时")
                time.sleep(0.5)
        try:
            # 拿到锁后重新检测：另一进程可能已完成迁移
            _run_migrations_locked()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _restore_missing_indexes() -> None:
    """create_all 不会给已有表补索引，表重建后索引也会丢失，这里统一补齐。"""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {ix["name"] for ix in inspector.get_indexes(table.name)}
        for ix in table.indexes:
            if ix.name not in existing:
                logger.info("迁移：补建索引 %s", ix.name)
                ix.create(bind=engine)


def _run_migrations_locked() -> None:
    # 确保所有模型已注册到 Base.metadata（database.py 本身不导入 models，避免循环依赖）
    from app import models as _models  # noqa: F401

    inspector = inspect(engine)
    pending: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = f"{column.name} {column.type.compile(engine.dialect)}{_default_literal(column)}"
            pending.append((f"ALTER TABLE {table.name} ADD COLUMN {ddl}", f"{table.name}.{column.name}"))
    need_rebuild = _accounts_need_rebuild(inspector)
    if not pending and not need_rebuild:
        _restore_missing_indexes()
        return
    _backup_sqlite()
    try:
        if pending:
            with engine.begin() as conn:
                for stmt, name in pending:
                    logger.info("迁移：为 %s 补充缺失列", name)
                    conn.exec_driver_sql(stmt)
                # 投递双通道子状态回填：ADD COLUMN 带 DEFAULT 会把存量行填成 'pending'（非 NULL），
                # 因此按整条 status 无条件映射：sent→sent、cancelled→cancelled、其余等待重试
                added = {name for _, name in pending}
                for col in ("email_status", "webhook_status"):
                    if f"deliveries.{col}" in added:
                        conn.exec_driver_sql(
                            f"UPDATE deliveries SET {col} = CASE status "
                            f"WHEN 'sent' THEN 'sent' WHEN 'cancelled' THEN 'cancelled' "
                            f"ELSE 'pending' END"
                        )
        if need_rebuild:
            _rebuild_xueqiu_accounts()
        _restore_missing_indexes()
    except Exception:
        logger.exception("数据库迁移执行失败（已有备份文件可手动恢复）")
        raise
