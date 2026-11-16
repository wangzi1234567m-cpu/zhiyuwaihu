"""Compatibility import; canonical definitions live in backend/app/contracts.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.contracts import *
