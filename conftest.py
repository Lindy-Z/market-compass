"""
Pytest rootdir config — make ``src/`` subpackages importable in tests without
requiring ``pip install -e .``.

pytest 根配置 — 让 ``src/`` 下的子包在测试中可直接导入, 无需先 ``pip install -e .``.

When we introduce a proper ``pyproject.toml`` with setuptools / hatch / etc.
(tracked as a Phase 2.x follow-up), this conftest becomes redundant and can
be removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
_src_str = str(_SRC)
if _src_str not in sys.path:
    sys.path.insert(0, _src_str)
