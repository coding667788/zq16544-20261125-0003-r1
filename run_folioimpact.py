#!/usr/bin/env python3
"""Repository-root launcher for isolated/Windows invocations.

Example:
    python -I run_folioimpact.py bound fixtures/case_replay.json
"""

import sys
from pathlib import Path

# Isolated mode can omit the script directory from sys.path for launchers.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folioimpact.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
