from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # agent-copilot/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    data_dir: Path = ROOT / "dataset" / "sample-data"
    dataset_id: str = "sample"
    active_dataset: str = "sample"
    clock_today: date = date(2026, 10, 17)
    db_path: Path = ROOT / "backend" / "wellops.duckdb"

    model_orchestrator: str = "openrouter:google/gemma-4-31b-it:free"
    model_worker: str = "openrouter:google/gemma-4-26b-a4b-it:free"


settings = Settings()
