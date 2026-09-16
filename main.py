"""FootyLevels entry point. See scripts/demo.py for the extraction CLI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))

from demo import main

if __name__ == "__main__":
    sys.exit(main())
