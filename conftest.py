"""
conftest.py — shared pytest fixtures and path setup for EPF Sentinel.

Adds src/ to sys.path so that `import shared.<module>` resolves correctly
from any test file without shadowing Python stdlib modules like `http`.
"""
import os
import sys
import pathlib

os.environ.setdefault("EPF_METRICS_DISABLED", "1")

_SRC = pathlib.Path(__file__).parent / "src"
_s = str(_SRC)
if _s not in sys.path:
    sys.path.insert(0, _s)
