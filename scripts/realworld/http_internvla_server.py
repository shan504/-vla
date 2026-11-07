"""Compatibility entry point; use ``python -m go2.server --help`` for options."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from go2.server import main


if __name__ == '__main__':
    main()
