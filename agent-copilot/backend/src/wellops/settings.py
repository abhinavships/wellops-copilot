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

    model_orchestrator: str = ""  # legacy pydantic-ai test path only
    model_worker: str = ""
    model_local: str = ""
    model_fallbacks: str = ""  # comma-separated, tried in order (FallbackModel)
    ollama_base_url: str = ""
    openrouter_api_key: str = ""
    opik_api_key: str = ""
    elevenlabs_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "gemini"  # Google OpenAI-compatible endpoint
    llm_model: str = ""  # default per provider below
    self_url: str = "http://127.0.0.1:8000"  # where this API (and its /mcp) is reachable from local agents
    harness: str = "native"  # native (Gemini agent over the WellOps tool registry) | pydantic (legacy test agent)
    opik_workspace: str = ""
    opik_project_name: str = "wellops"
    opik_url_override: str = ""
    otel_exporter_otlp_endpoint: str = ""

    @property
    def fallback_models(self) -> list[str]:
        listed = [m.strip() for m in self.model_fallbacks.split(",") if m.strip()]
        return listed or [self.model_orchestrator, self.model_worker]


settings = Settings()
