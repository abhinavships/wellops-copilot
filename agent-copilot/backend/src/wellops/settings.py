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
    model_local: str = ""
    model_fallbacks: str = ""  # comma-separated, tried in order (FallbackModel)
    ollama_base_url: str = ""
    openrouter_api_key: str = ""
    opik_api_key: str = ""
    opik_workspace: str = ""
    opik_project_name: str = "wellops"
    opik_url_override: str = ""
    otel_exporter_otlp_endpoint: str = ""

    @property
    def fallback_models(self) -> list[str]:
        listed = [m.strip() for m in self.model_fallbacks.split(",") if m.strip()]
        return listed or [self.model_orchestrator, self.model_worker]


settings = Settings()
