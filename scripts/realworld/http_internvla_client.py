"""Compatibility entry point; see go2.robot_client for the configured Go2 client."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from go2.robot_client import main


if __name__ == '__main__':
    main()
