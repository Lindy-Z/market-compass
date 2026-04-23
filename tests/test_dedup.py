"""
Tests for src/processing/dedup.py — content-hash deduplication.
去重模块测试。

Two groups:
  * pure-function hash contract (determinism, field sensitivity,
    normalization, error paths)
  * integration against the SQLite archive (is_duplicate, filter_new)
"""
from __future__ import annotations

import pytest

from processing.dedup import (
    BODY_TRUNCATE_CHARS,
    content_hash,
    filter_new,
    is_duplicate,
)
from storage.db import get_connection, init_db

ISO_TS = "2026-04-23T12:00:00Z"


# =============================================================================
# content_hash — pure-function contract
# =============================================================================

def test_hash_is_deterministic() -> None:
    h1 = content_hash("Fed raises rates", "Body text", "reuters-rss", ISO_TS)
    h2 = content_hash("Fed raises rates", "Body text", "reuters-rss", ISO_TS)
    assert h1 == h2


def test_hash_format_is_64_hex_lowercase() -> None:
    h = content_hash("T", "B", "s", ISO_TS)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_changes_with_title() -> None:
    h1 = content_hash("Fed raises rates", "B", "s", ISO_TS)
    h2 = content_hash("Fed CUTS rates", "B", "s", ISO_TS)
    assert h1 != h2


def test_hash_changes_with_body() -> None:
    h1 = content_hash("T", "Body A", "s", ISO_TS)
    h2 = content_hash("T", "Body B", "s", ISO_TS)
    assert h1 != h2


def test_hash_changes_with_source() -> None:
    """Cross-source variants must hash differently — keep both in archive."""
    h_reu = content_hash("T", "B", "reuters-rss", ISO_TS)
    h_blo = content_hash("T", "B", "bloomberg", ISO_TS)
    assert h_reu != h_blo


def test_hash_changes_with_pub_ts() -> None:
    h1 = content_hash("T", "B", "s", "2026-04-23T12:00:00Z")
    h2 = content_hash("T", "B", "s", "2026-04-23T13:00:00Z")
    assert h1 != h2


def test_hash_normalizes_whitespace_in_title() -> None:
    """Extra/collapsed whitespace must not change the hash."""
    h1 = content_hash("  Fed   raises\trates\n", "B", "s", ISO_TS)
    h2 = content_hash("Fed raises rates", "B", "s", ISO_TS)
    assert h1 == h2


def test_hash_normalizes_whitespace_in_body() -> None:
    h1 = content_hash("T", "line1\n\n\nline2", "s", ISO_TS)
    h2 = content_hash("T", "line1 line2", "s", ISO_TS)
    assert h1 == h2


def test_hash_treats_None_and_empty_body_as_same() -> None:
    h_none = content_hash("T", None, "s", ISO_TS)
    h_empty = content_hash("T", "", "s", ISO_TS)
    assert h_none == h_empty


def test_hash_truncates_body_at_codepoint_limit() -> None:
    """
    Two bodies that share the first BODY_TRUNCATE_CHARS codepoints must
    hash identically regardless of what follows.
    """
    prefix = "a" * BODY_TRUNCATE_CHARS
    h1 = content_hash("T", prefix + "X" * 100, "s", ISO_TS)
    h2 = content_hash("T", prefix + "Y" * 500, "s", ISO_TS)
    assert h1 == h2


def test_hash_diverges_inside_truncation_window() -> None:
    """A single-char difference within the first 2048 chars must differ."""
    body1 = "a" * (BODY_TRUNCATE_CHARS - 1) + "Z"
    body2 = "a" * (BODY_TRUNCATE_CHARS - 1) + "W"
    assert content_hash("T", body1, "s", ISO_TS) != content_hash("T", body2, "s", ISO_TS)


def test_hash_handles_chinese_deterministically() -> None:
    h1 = content_hash("美联储加息", "关于货币政策的新闻", "caixin", ISO_TS)
    h2 = content_hash("美联储加息", "关于货币政策的新闻", "caixin", ISO_TS)
    assert h1 == h2


def test_hash_nfc_normalization_unifies_composed_and_decomposed() -> None:
    """
    Composed (U+00E9) and decomposed (U+0065 + U+0301) forms of 'é' must
    produce the same hash after NFC normalization.
    """
    composed = "caf\u00e9"
    decomposed = "cafe\u0301"
    assert composed != decomposed  # literally different strings
    h1 = content_hash(composed, "B", "s", ISO_TS)
    h2 = content_hash(decomposed, "B", "s", ISO_TS)
    assert h1 == h2


def test_hash_rejects_empty_title() -> None:
    with pytest.raises(ValueError, match="title"):
        content_hash("", "B", "s", ISO_TS)
    with pytest.raises(ValueError, match="title"):
        content_hash("   \n\t  ", "B", "s", ISO_TS)


def test_hash_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="source"):
        content_hash("T", "B", "", ISO_TS)


def test_hash_rejects_empty_pub_ts() -> None:
    with pytest.raises(ValueError, match="pub_ts"):
        content_hash("T", "B", "s", "")


# =============================================================================
# is_duplicate — DB lookup
# =============================================================================

def test_is_duplicate_false_for_missing_hash() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        assert is_duplicate(conn, "0" * 64) is False


def test_is_duplicate_true_for_existing_hash() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        h = content_hash("T", "B", "s", ISO_TS)
        conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            (h, "s", "T", ISO_TS, ISO_TS),
        )
        assert is_duplicate(conn, h) is True


# =============================================================================
# filter_new — batch dedup against DB + within batch
# =============================================================================

def test_filter_new_skips_items_already_in_db() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        h_existing = content_hash("Existing", "B", "src1", ISO_TS)
        conn.execute(
            "INSERT INTO items (content_hash, source, title, pub_ts, "
            "fetched_ts) VALUES (?, ?, ?, ?, ?)",
            (h_existing, "src1", "Existing", ISO_TS, ISO_TS),
        )

        batch = [
            {"title": "Existing", "body": "B", "source": "src1", "pub_ts": ISO_TS},
            {"title": "New", "body": "B", "source": "src1", "pub_ts": ISO_TS},
        ]
        new = list(filter_new(conn, batch))

        assert len(new) == 1
        assert new[0]["title"] == "New"


def test_filter_new_dedupes_within_batch() -> None:
    """Two identical items in the same batch should yield exactly one."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        batch = [
            {"title": "T", "body": "B", "source": "s", "pub_ts": ISO_TS},
            {"title": "T", "body": "B", "source": "s", "pub_ts": ISO_TS},
        ]
        new = list(filter_new(conn, batch))
        assert len(new) == 1


def test_filter_new_attaches_content_hash_key() -> None:
    """Output dicts should carry a 'content_hash' key for downstream insert."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        batch = [{"title": "T", "body": "B", "source": "s", "pub_ts": ISO_TS}]
        new = list(filter_new(conn, batch))
        assert new[0]["content_hash"] == content_hash("T", "B", "s", ISO_TS)


def test_filter_new_preserves_extra_keys() -> None:
    """Keys outside the hash contract (e.g. 'source_url') pass through."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        batch = [
            {
                "title": "T",
                "body": "B",
                "source": "s",
                "pub_ts": ISO_TS,
                "source_url": "https://example.com/x",
                "meta": {"lang": "en"},
            }
        ]
        new = list(filter_new(conn, batch))
        assert new[0]["source_url"] == "https://example.com/x"
        assert new[0]["meta"] == {"lang": "en"}


def test_filter_new_does_not_mutate_input() -> None:
    """The yielded dict is a copy; the original stays untouched."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        orig = {"title": "T", "body": "B", "source": "s", "pub_ts": ISO_TS}
        list(filter_new(conn, [orig]))
        assert "content_hash" not in orig


def test_filter_new_accepts_generator() -> None:
    """Streaming: a generator input must work (single-pass iteration)."""
    def gen():
        yield {"title": "A", "body": "B", "source": "s", "pub_ts": ISO_TS}
        yield {"title": "B", "body": "B", "source": "s", "pub_ts": ISO_TS}

    with get_connection(":memory:") as conn:
        init_db(conn)
        new = list(filter_new(conn, gen()))
        assert len(new) == 2


def test_filter_new_cross_source_kept_separate() -> None:
    """
    Same title+body+pub_ts from different sources must both pass through —
    source is part of the hash on purpose (see module docstring).
    """
    with get_connection(":memory:") as conn:
        init_db(conn)
        batch = [
            {"title": "Fed hike", "body": "B", "source": "reuters-rss", "pub_ts": ISO_TS},
            {"title": "Fed hike", "body": "B", "source": "bloomberg",   "pub_ts": ISO_TS},
        ]
        new = list(filter_new(conn, batch))
        assert len(new) == 2
        assert {n["source"] for n in new} == {"reuters-rss", "bloomberg"}
