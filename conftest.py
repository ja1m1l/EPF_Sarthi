"""
conftest.py ? shared pytest fixtures and path setup for EPF Sentinel.

Adds src/shared to sys.path so that `import models` and `import shared.logging`
resolve correctly from any test file, without requiring an install step.
"""
import sys
import pathlib

# Repo root / src / shared  -->  importable as both:
#   import models           (direct module)
#   import shared.logging   (package-qualified)
_SHARED = pathlib.Path(__file__).parent / "src" / "shared"
_SRC    = pathlib.Path(__file__).parent / "src"

for _p in (_SHARED, _SRC):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)
