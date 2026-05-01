"""Tests for src/util/env.py — minimal .env loader."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from util.env import load_dotenv


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


# =============================================================================
# Basic parsing
# =============================================================================

def test_loads_simple_assignment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    p = _write(tmp_path, "FOO=bar\n")
    loaded = load_dotenv(p)
    assert os.environ["FOO"] == "bar"
    assert loaded == {"FOO": "bar"}


def test_loads_double_quoted_value_with_spaces(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("UA", raising=False)
    p = _write(tmp_path, 'UA="market-compass/0.1 (contact: x@y.com)"\n')
    load_dotenv(p)
    assert os.environ["UA"] == "market-compass/0.1 (contact: x@y.com)"


def test_loads_single_quoted_value(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("KEY", raising=False)
    p = _write(tmp_path, "KEY='value with spaces'\n")
    load_dotenv(p)
    assert os.environ["KEY"] == "value with spaces"


def test_loads_unquoted_value_with_parens(tmp_path, monkeypatch) -> None:
    """SEC_EDGAR_USER_AGENT is the realistic test — has '(', ')', spaces."""
    monkeypatch.delenv("UA", raising=False)
    p = _write(tmp_path, "UA=market-compass/0.1 (contact: x@y.com)\n")
    load_dotenv(p)
    assert os.environ["UA"] == "market-compass/0.1 (contact: x@y.com)"


def test_tolerates_export_prefix(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    p = _write(tmp_path, "export FOO=bar\n")
    load_dotenv(p)
    assert os.environ["FOO"] == "bar"


def test_handles_whitespace_around_equals(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    p = _write(tmp_path, "FOO   =   bar\n")
    load_dotenv(p)
    assert os.environ["FOO"] == "bar"


def test_skips_comment_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    p = _write(tmp_path, "# this is a comment\nFOO=bar\n# another\n")
    loaded = load_dotenv(p)
    assert os.environ["FOO"] == "bar"
    assert loaded == {"FOO": "bar"}


def test_skips_blank_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    p = _write(tmp_path, "\n\nFOO=1\n\n\nBAR=2\n\n")
    load_dotenv(p)
    assert os.environ["FOO"] == "1"
    assert os.environ["BAR"] == "2"


def test_loads_multiple_vars(tmp_path, monkeypatch) -> None:
    for k in ("A", "B", "C"):
        monkeypatch.delenv(k, raising=False)
    p = _write(tmp_path, "A=1\nB=two\nC='three'\n")
    loaded = load_dotenv(p)
    assert loaded == {"A": "1", "B": "two", "C": "three"}
    assert os.environ["A"] == "1"
    assert os.environ["B"] == "two"
    assert os.environ["C"] == "three"


# =============================================================================
# Override behavior
# =============================================================================

def test_does_not_override_existing_env_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOO", "shell_set")
    p = _write(tmp_path, "FOO=dotenv_set\n")
    loaded = load_dotenv(p)
    assert os.environ["FOO"] == "shell_set"
    assert loaded == {}  # nothing was set


def test_override_true_replaces_existing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FOO", "shell_set")
    p = _write(tmp_path, "FOO=dotenv_set\n")
    loaded = load_dotenv(p, override=True)
    assert os.environ["FOO"] == "dotenv_set"
    assert loaded == {"FOO": "dotenv_set"}


# =============================================================================
# Missing file + malformed lines
# =============================================================================

def test_missing_file_silent_by_default(tmp_path) -> None:
    """No .env? Return empty, no exception, no stderr."""
    bogus = tmp_path / "does_not_exist.env"
    loaded = load_dotenv(bogus)
    assert loaded == {}


def test_missing_file_warns_when_quiet_false(tmp_path, capsys) -> None:
    bogus = tmp_path / "does_not_exist.env"
    load_dotenv(bogus, quiet=False)
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_malformed_lines_are_skipped(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OK", raising=False)
    p = _write(tmp_path, "this is not an assignment\nOK=yes\n=no_key\n123BAD=x\n")
    loaded = load_dotenv(p)
    assert loaded == {"OK": "yes"}


# =============================================================================
# Realistic .env content (mirrors .env.example shape)
# =============================================================================

def test_loads_realistic_env_content(tmp_path, monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "FRED_API_KEY", "FINNHUB_API_KEY",
              "SEC_EDGAR_USER_AGENT", "LOCAL_TZ"):
        monkeypatch.delenv(k, raising=False)
    p = _write(tmp_path, '''# market-compass .env

ANTHROPIC_API_KEY=sk-test-key-shape
FRED_API_KEY=abcdef0123
FINNHUB_API_KEY=0123abcdef
SEC_EDGAR_USER_AGENT=market-compass/0.1 (contact: x@y.com)
LOCAL_TZ=America/Toronto
''')
    loaded = load_dotenv(p)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-key-shape"
    assert os.environ["FRED_API_KEY"] == "abcdef0123"
    assert os.environ["FINNHUB_API_KEY"] == "0123abcdef"
    assert os.environ["SEC_EDGAR_USER_AGENT"] == \
        "market-compass/0.1 (contact: x@y.com)"
    assert os.environ["LOCAL_TZ"] == "America/Toronto"
    assert len(loaded) == 5
