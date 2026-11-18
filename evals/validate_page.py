#!/usr/bin/env python3
"""Moved: the validator ships with the skill so installed copies can run it.

This shim keeps `python3 evals/validate_page.py PAGE.html [--browser]` and
`import validate_page` working for the eval suites. The real file is
plugins/explain-diff/skills/explain-diff/scripts/validate_page.py.
"""
import importlib.util
import os
import sys

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "explain-diff", "skills",
                     "explain-diff", "scripts", "validate_page.py")
_spec = importlib.util.spec_from_file_location("_explain_diff_validate_page", _PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    sys.argv[0] = _PATH
    _mod.main()
