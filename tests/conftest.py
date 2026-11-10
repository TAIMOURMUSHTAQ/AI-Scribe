import os
import sys
from pathlib import Path

os.environ.setdefault("AI_PROVIDER", "mock")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
