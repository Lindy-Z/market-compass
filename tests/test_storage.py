"""
Tests for src/storage/db.py + schema.sql.
存储层测试。

All tests run against an in-memory SQLite (:memory:) so they are fast and
leave nothing on disk.
所有测试都跑在 :memory: 库上,快速且不落盘。
"""
from __future__ import annotations

import sqlite3

import pytest

from storage.db import (
    SCHEMA_VERSION,
    get_connection,
    init_db,
    schema_version,
)

ISO_TS = "2026-01-01T00:00:00Z"


# -----------------------------------------------------------------------------
# Schema creation
# -----------------------------------------------------------------------------

def test_init_creates_all_expected_tables() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    # sqlite_sequence is auto-created by AUTOINCREMENT columns — expected.
    assert {"items", "runs", "deliveries", "triggers"}.issubset(tables)


def test_init_creates_indexes() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
    expected = {
        "idx_items_pub_ts",
        "idx_items_track",
        "idx_items_importance",
        "idx_items_fetched_ts",
        "idx_runs_started_ts",
        "idx_runs_run_type",
        "idx_deliveries_run_id",
        "idx_triggers_item_id",
        "idx_triggers_fired_ts",
        "idx_triggers_kind",
    }
    assert expected.issubset(indexes)


def test_init_stamps_schema_version() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        assert schema_version(conn) == SCHEMA_VERSION


def test_init_is_idempotent() -> None:
    """Running init_db twice on the same connection must not raise."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        init_db(conn)  # second call
        assert schema_version(conn) == SCHEMA_VERSION


def test_init_refuses_newer_db() -> None:
    """If user_version > SCHEMA_VERSION, init_db must refuse."""
    with get_connection(":memory:") as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with pytest.raises(RuntimeError, match="newer than code expects"):
            init_db(conn)


# -----------------------------------------------------------------------------
# CHECK constraints
# -----------------------------------------------------------------------------

def test_items_track_check_rejects_invalid() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO items (content_hash, source, title, pub_ts, "
                "fetched_ts, track) VALUES (?, ?, ?, ?, ?, ?)",
                ("h1", "test", "t", ISO_TS, ISO_TS, "invalid_track"),
            )


def test_items_track_allows_null() -> None:
    """track can be NULL (before classification runs)."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            ("h_null_track", "test", "t", ISO_TS, ISO_TS),
        )


def test_items_importance_range_enforced() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO items (content_hash, source, title, pub_ts, "
                "fetched_ts, importance) VALUES (?, ?, ?, ?, ?, ?)",
                ("h2", "test", "t", ISO_TS, ISO_TS, 150),
            )


def test_runs_run_type_check() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_type, started_ts, status) "
                "VALUES (?, ?, ?)",
                ("hourly", ISO_TS, "ok"),
            )


def test_runs_status_check() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_type, started_ts, status) "
                "VALUES (?, ?, ?)",
                ("daily", ISO_TS, "weird_status"),
            )


def test_deliveries_channel_check() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO runs (run_type, started_ts, status) VALUES "
            "(?, ?, ?)",
            ("daily", ISO_TS, "ok"),
        )
        run_id = cur.lastrowid
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO deliveries (run_id, channel, ok, attempt, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, "sms", 1, 1, ISO_TS),  # sms not allowed
            )


# -----------------------------------------------------------------------------
# Uniqueness & foreign-key cascade
# -----------------------------------------------------------------------------

def test_content_hash_is_unique() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            ("dup", "test", "t1", ISO_TS, ISO_TS),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO items (content_hash, source, title, pub_ts, "
                "fetched_ts) VALUES (?, ?, ?, ?, ?)",
                ("dup", "test", "t2", ISO_TS, ISO_TS),
            )


def test_fk_cascade_run_to_deliveries() -> None:
    """Deleting a run must cascade to its deliveries."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO runs (run_type, started_ts, status) "
            "VALUES (?, ?, ?)",
            ("daily", ISO_TS, "ok"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO deliveries (run_id, channel, ok, attempt, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, "telegram", 1, 1, ISO_TS),
        )
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM deliveries"
        ).fetchone()["c"] == 1

        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

        assert conn.execute(
            "SELECT COUNT(*) AS c FROM deliveries"
        ).fetchone()["c"] == 0


def test_fk_cascade_item_to_triggers() -> None:
    """Deleting an item must cascade to its triggers."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            ("for_trigger", "test", "t", ISO_TS, ISO_TS),
        )
        item_id = cur.lastrowid
        conn.execute(
            "INSERT INTO triggers (item_id, kind, fired_ts) "
            "VALUES (?, ?, ?)",
            (item_id, "deal_size", ISO_TS),
        )
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM triggers"
        ).fetchone()["c"] == 1

        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

        assert conn.execute(
            "SELECT COUNT(*) AS c FROM triggers"
        ).fetchone()["c"] == 0


def test_fk_set_null_run_to_triggers() -> None:
    """triggers.run_id is ON DELETE SET NULL (not CASCADE)."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            ("itm", "test", "t", ISO_TS, ISO_TS),
        )
        item_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO runs (run_type, started_ts, status) "
            "VALUES (?, ?, ?)",
            ("trigger", ISO_TS, "ok"),
        )
        run_id = cur.lastrowid
        conn.execute(
            "INSERT INTO triggers (item_id, kind, fired_ts, run_id) "
            "VALUES (?, ?, ?, ?)",
            (item_id, "cb_surprise", ISO_TS, run_id),
        )
        # Delete the run — trigger should survive with NULL run_id.
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        row = conn.execute(
            "SELECT run_id FROM triggers WHERE item_id = ?", (item_id,)
        ).fetchone()
        assert row is not None
        assert row["run_id"] is None


# -----------------------------------------------------------------------------
# Rollback semantics of the context manager
# -----------------------------------------------------------------------------

def test_context_manager_rolls_back_on_exception(tmp_path) -> None:
    """
    get_connection should roll back uncommitted work when the body raises.
    Uses an on-disk tmp DB (pytest's tmp_path, which cleans up resiliently)
    so the rollback is observable across reconnects.

    上下文管理器遇到异常时应回滚未提交的写入。用 pytest 的 tmp_path 在磁盘上
    建库,跨连接验证回滚效果。
    """
    db = tmp_path / "rollback_test.db"

    # First connection: init DB + start a write that raises before commit.
    with pytest.raises(RuntimeError, match="boom"):
        with get_connection(db) as conn:
            init_db(conn)
            conn.execute(
                "INSERT INTO items (content_hash, source, title, "
                "pub_ts, fetched_ts) VALUES (?, ?, ?, ?, ?)",
                ("rolled_back", "test", "t", ISO_TS, ISO_TS),
            )
            raise RuntimeError("boom")

    # Second connection: schema persists (DDL auto-commits via executescript),
    # but the INSERT did NOT — the context manager rolled it back.
    with get_connection(db) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE content_hash = ?",
            ("rolled_back",),
        ).fetchone()
        assert row["c"] == 0


def test_context_manager_commits_on_clean_exit(tmp_path) -> None:
    """
    Conversely: a clean exit from the ``with`` block must commit writes
    so they survive reconnecting.
    """
    db = tmp_path / "commit_test.db"

    with get_connection(db) as conn:
        init_db(conn)
        conn.execute(
            "INSERT INTO items (content_hash, source, title, "
            "pub_ts, fetched_ts) VALUES (?, ?, ?, ?, ?)",
            ("persisted", "test", "t", ISO_TS, ISO_TS),
        )

    with get_connection(db) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE content_hash = ?",
            ("persisted",),
        ).fetchone()
        assert row["c"] == 1
