"""Test configuration.

Puts `tests/` on `sys.path` so the shared `fixtures` package imports the same
way regardless of pytest's import mode or the rootdir it infers.
"""

import pathlib
import sys

TESTS_DIR = str(pathlib.Path(__file__).resolve().parent)

if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)
