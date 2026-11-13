#!/usr/bin/env python
"""Development launcher: `python run.py`."""

from __future__ import annotations

import sys

from modpilot.app import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
