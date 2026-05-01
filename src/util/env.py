"""
market-compass — minimal .env loader
==============================================================================
``.env`` 文件加载器,无外部依赖。

Why not python-dotenv? / 为什么不用 python-dotenv?
- One more dep on the surface.
- We need ~30 lines of logic; the lib is overkill for our use.
- Self-rolled means we know exactly what shell-special characters are
  handled (e.g. unquoted parens in ``SEC_EDGAR_USER_AGENT``).

Format / 格式:

    KEY=value
    KEY="value with spaces or specials like (parens)"
    KEY='single-quoted is fine too'
    export KEY=value          # shell-style 'export' prefix tolerated
    # comment lines starting with # are skipped
                              # blank lines OK

By default does NOT override variables already set in ``os.environ``,
so explicit shell exports always win.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Union

# Match: optional leading whitespace, key, =, value (capturing greedy
# until trailing whitespace/EOL).
# Key rules: must start with letter or underscore; ASCII identifiers only.
_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(
    path: Union[str, Path] = ".env",
    *,
    override: bool = False,
    quiet: bool = True,
) -> dict[str, str]:
    """
    Load env variables from a ``.env`` file into ``os.environ``.

    Args:
        path: file path (default ``.env`` relative to cwd).
        override: when ``True``, replace already-set ``os.environ`` keys.
            Default ``False`` — explicit shell exports always win.
        quiet: when ``True`` (default), missing file is a silent no-op.
            ``False`` prints a warning to stderr.

    Returns:
        Dict of variables this call ACTUALLY set (excludes vars skipped
        because they were already in ``os.environ`` and ``override`` was
        ``False``).

    Raises:
        Nothing — malformed lines are skipped.
    """
    p = Path(path)
    if not p.is_file():
        if not quiet:
            print(f"load_dotenv: {p} not found, skipping", file=sys.stderr)
        return {}

    loaded: dict[str, str] = {}

    for raw_line in p.read_text(encoding="utf-8").splitlines():
        # Skip blank and comment lines (we don't try to handle inline
        # comments — if you want a `#` in a value, quote it).
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Tolerate shell-style 'export KEY=...' prefix.
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()

        m = _LINE_RE.match(stripped)
        if not m:
            continue

        key = m.group(1)
        value = m.group(2)

        # Strip matching surrounding quotes if present.
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]

        # Respect existing env unless override requested.
        if not override and key in os.environ:
            continue

        os.environ[key] = value
        loaded[key] = value

    return loaded


__all__ = ["load_dotenv"]
