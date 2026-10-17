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

    model_orchestrator: str = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
    model_worker: str = "openrouter:poolside/laguna-s-2.1:free"


settings = Settings()
