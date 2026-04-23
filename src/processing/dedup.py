"""
market-compass processing — content-hash deduplication
==============================================================================
内容哈希去重 / Content-hash deduplication

Hash contract / 哈希契约
------------------------
    SHA-256 hex digest of the NUL-joined, normalized tuple:

        NFC(strip(collapse_ws(title)))      \\x00
        NFC(collapse_ws(body[:2048]))       \\x00
        NFC(collapse_ws(source))            \\x00
        NFC(collapse_ws(pub_ts))

    - ``body`` is treated as ``""`` when ``None``.
    - ``body[:2048]`` slices **codepoints**, not bytes — safe for Chinese.
    - Normalization = Unicode NFC + collapse any run of whitespace to a
      single space + strip ends. This absorbs feed-level formatting noise
      (line endings, extra spaces) without merging semantically different
      text.

Why these choices / 为什么这么设计
--------------------------------
- **source is part of the hash**: same story from Reuters vs. Bloomberg
  produces different hashes; we keep BOTH so the reasoning layer can treat
  cross-source agreement as a signal. Hard dedup only fires when the SAME
  source re-polls the SAME item. Cross-source clustering is a separate
  (later) concern.
  **source 参与哈希**: 同一事件的路透 vs. 彭博两个报道会得到两个哈希,两条都
  保留,由推理层识别跨源一致性信号。硬去重只对同源重复生效。
- **pub_ts is part of the hash**: prevents "republished" stories (same
  title, same source, different date) from masking a fresh story.
  **pub_ts 参与哈希**: 防止同一来源重发旧稿 (同标题不同日期) 遮盖真正新的新闻。
- **body truncated to 2048 codepoints**: avoids whole-article changes
  (typo fix late in an article) from triggering a new hash. 2048 chars is
  enough to anchor identity.
  **body 截取到 2048 字符**: 避免文章末尾的微小修订触发新哈希。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Iterator

# -----------------------------------------------------------------------------
# Constants / 常量
# -----------------------------------------------------------------------------

#: Maximum body length (in Unicode codepoints) that participates in the hash.
#: Beyond this, changes don't affect dedup. Chosen to absorb late-breaking
#: edits while still anchoring identity.
#: 参与哈希的 body 最大字符数 (codepoint)。超过的部分改动不影响去重。
BODY_TRUNCATE_CHARS: int = 2048

_WHITESPACE_RUN: re.Pattern[str] = re.compile(r"\s+")


# -----------------------------------------------------------------------------
# Normalization / 标准化
# -----------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """
    Canonicalize a text input for hashing.

    Steps / 步骤:
        1. Unicode NFC composition — stable representation of Chinese and
           accented Latin characters across feeds.
        2. Collapse any run of whitespace (spaces, tabs, newlines, etc.)
           to a single space.
        3. Strip leading/trailing whitespace.
    """
    text = unicodedata.normalize("NFC", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


# -----------------------------------------------------------------------------
# Public API / 公共 API
# -----------------------------------------------------------------------------

def content_hash(
    title: str,
    body: str | None,
    source: str,
    pub_ts: str,
) -> str:
    """
    Compute the deterministic SHA-256 hex digest used as the dedup key.

    Args:
        title: headline. Required.
        body: article body, or ``None`` / ``""`` for headline-only items.
        source: source identifier, e.g. ``"reuters-rss"``. Required.
        pub_ts: ISO-8601 UTC timestamp string, e.g.
            ``"2026-04-23T12:34:56Z"``. Required.

    Returns:
        A 64-character lowercase hex string.

    Raises:
        ValueError: if ``title``, ``source``, or ``pub_ts`` normalizes to
            an empty string.
    """
    title_n = _normalize(title)
    body_raw = body or ""
    body_n = _normalize(body_raw[:BODY_TRUNCATE_CHARS])
    source_n = _normalize(source)
    pub_ts_n = _normalize(pub_ts)

    if not title_n:
        raise ValueError("content_hash: title must be non-empty after normalization")
    if not source_n:
        raise ValueError("content_hash: source must be non-empty after normalization")
    if not pub_ts_n:
        raise ValueError("content_hash: pub_ts must be non-empty after normalization")

    payload = "\x00".join([title_n, body_n, source_n, pub_ts_n])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_duplicate(conn: sqlite3.Connection, h: str) -> bool:
    """
    ``True`` iff an item with ``content_hash == h`` is already in
    ``items``.

    Args:
        conn: open SQLite connection against an init'd DB.
        h: a 64-char hex hash (as returned by :func:`content_hash`).

    Returns:
        Bool indicating existence.
    """
    row = conn.execute(
        "SELECT 1 FROM items WHERE content_hash = ? LIMIT 1",
        (h,),
    ).fetchone()
    return row is not None


def filter_new(
    conn: sqlite3.Connection,
    items: Iterable[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """
    Stream only the items whose content hash is not yet archived AND is
    not a duplicate of an earlier item in the same batch.

    Each input dict must carry at least:
        ``title``, ``source``, ``pub_ts``  (all non-empty)
    May carry:
        ``body``  (optional; ``None`` treated as ``""``)
        plus any other keys, which pass through untouched.

    Each yielded dict is a shallow copy of the input, augmented with a
    ``"content_hash"`` key so downstream insertion can reuse the hash
    instead of recomputing it.

    Streaming is safe to pass a generator. Intra-batch duplicates are
    dropped after the first occurrence.

    Args:
        conn: open SQLite connection against an init'd DB.
        items: iterable of input dicts (see above).

    Yields:
        Dicts whose ``content_hash`` is new to both the DB and the batch.
    """
    seen_in_batch: set[str] = set()
    for item in items:
        h = content_hash(
            title=item["title"],
            body=item.get("body"),
            source=item["source"],
            pub_ts=item["pub_ts"],
        )
        if h in seen_in_batch:
            continue
        if is_duplicate(conn, h):
            continue
        seen_in_batch.add(h)
        out = dict(item)
        out["content_hash"] = h
        yield out


__all__ = [
    "BODY_TRUNCATE_CHARS",
    "content_hash",
    "is_duplicate",
    "filter_new",
]
