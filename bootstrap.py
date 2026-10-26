"""Entry point used by the bundled Windows runtime; no user profile writes."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from scriptolib.__main__ import main

raise SystemExit(main())

