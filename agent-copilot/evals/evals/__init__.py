"""WellOps evaluation system (stream D). Offline-first; Opik logging is opt-in."""
from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parents[1]          # agent-copilot/evals
REPO_ROOT = EVALS_ROOT.parent                              # agent-copilot
CASES_DIR = EVALS_ROOT / "cases"
RESULTS_DIR = EVALS_ROOT / "results"
SAMPLE_DATA = REPO_ROOT / "dataset" / "sample-data"
CACHE_DIR = EVALS_ROOT / ".cache"
