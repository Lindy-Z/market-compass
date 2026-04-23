"""
market-compass storage layer — SQLite connection factory & schema init
==============================================================================
存储层 — SQLite 连接工厂与 schema 初始化

This module is intentionally small: open a connection, ensure the schema
exists, commit or rollback cleanly. Higher-level archive logic (dedup,
retention, rollups) will live alongside it in later Phase 2 modules.

本模块刻意保持简洁: 开连接、确保 schema 存在、干净地提交或回滚。更高层的
归档逻辑 (去重、保留策略、时序列汇总) 将在 Phase 2 后续模块中加入。

Usage:

    from storage.db import get_connection, init_db

    with get_connection(":memory:") as conn:
        init_db(conn)
        # ... normal sqlite3 usage; commits on clean exit, rolls back on error
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# -----------------------------------------------------------------------------
# Module-level constants / 模块级常量
# -----------------------------------------------------------------------------

#: Schema version this module expects. Bump when you change schema.sql in a
#: non-additive way, and add a migration here.
#: 本模块期望的 schema 版本,schema.sql 非累加式改动时递增并补迁移逻辑。
SCHEMA_VERSION: int = 1

#: Path to the DDL script used by init_db().
SCHEMA_FILE: Path = Path(__file__).parent / "schema.sql"

#: Default on-disk DB location; overridable via MARKET_COMPASS_DB env var.
#: 默认磁盘位置,可由 MARKET_COMPASS_DB 环境变量覆盖。
DEFAULT_DB_PATH: Path = Path(
    os.environ.get("MARKET_COMPASS_DB", "data/market_compass.db")
)


# -----------------------------------------------------------------------------
# Connection / 连接
# -----------------------------------------------------------------------------

@contextmanager
def get_connection(
    db_path: Path | str | None = None,
) -> Iterator[sqlite3.Connection]:
    """
    Context-managed SQLite connection.

    - Commits on clean exit from the `with` block.
    - Rolls back and re-raises on exception.
    - Enables foreign-key enforcement on every connection.
    - Creates parent directories for on-disk DBs.
    - Pass ``":memory:"`` for an ephemeral in-memory DB (tests).

    受上下文管理的 SQLite 连接: 正常退出时提交,异常时回滚并上抛;每连接强制开启
    外键;磁盘库自动创建父目录;传 ``":memory:"`` 得到用于测试的内存库。

    Args:
        db_path: file path, ``":memory:"``, or ``None`` to use
            ``DEFAULT_DB_PATH``.

    Yields:
        An open ``sqlite3.Connection`` with ``row_factory = sqlite3.Row``.
    """
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    uri = str(resolved)

    if uri != ":memory:":
        Path(uri).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(uri, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Schema management / Schema 管理
# -----------------------------------------------------------------------------

def schema_version(conn: sqlite3.Connection) -> int:
    """Return the current ``PRAGMA user_version`` on ``conn``."""
    row = conn.execute("PRAGMA user_version").fetchone()
    # row can be sqlite3.Row or tuple depending on row_factory; both index 0.
    return int(row[0])


def init_db(conn: sqlite3.Connection) -> None:
    """
    Idempotently create tables and indexes; set WAL journal mode; stamp
    schema version.

    幂等地创建表与索引;设置 WAL 日志模式;标记 schema 版本。

    Safe to call against a fresh DB *or* an already-initialized DB. If the
    DB carries a ``user_version`` newer than ``SCHEMA_VERSION``, raises
    (older code must not silently "downgrade" a newer DB).

    Args:
        conn: an open SQLite connection.

    Raises:
        RuntimeError: if the DB's ``user_version`` > ``SCHEMA_VERSION``.
    """
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"DB user_version={current} is newer than code expects "
            f"(SCHEMA_VERSION={SCHEMA_VERSION}). Refusing to open — "
            f"the running code may be older than the DB. "
            f"数据库 user_version={current} 高于代码 SCHEMA_VERSION="
            f"{SCHEMA_VERSION},拒绝打开。"
        )

    # WAL: enable concurrent readers while a writer is active.
    # Silently no-ops on :memory: DBs (they only support 'memory' journal).
    conn.execute("PRAGMA journal_mode = WAL")

    ddl = SCHEMA_FILE.read_text(encoding="utf-8")
    conn.executescript(ddl)

    # Stamp the version. executescript commits any open tx; this pragma
    # runs cleanly afterward.
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_FILE",
    "DEFAULT_DB_PATH",
    "get_connection",
    "init_db",
    "schema_version",
]
