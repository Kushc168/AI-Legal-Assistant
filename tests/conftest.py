import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated data directory and offline mode for every test run (no network, no API key needed).
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="legal-assistant-test-")
os.environ["LLM_PROVIDER"] = "offline"
