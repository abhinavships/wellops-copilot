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

    model_orchestrator: str = ""  # legacy pydantic-ai path only; the live harness is Hermes + Gemini
    model_worker: str = ""
    model_local: str = ""
    model_fallbacks: str = ""  # comma-separated, tried in order (FallbackModel)
    ollama_base_url: str = ""
    openrouter_api_key: str = ""
    opik_api_key: str = ""
    elevenlabs_api_key: str = ""
    gemini_api_key: str = ""
    hermes_provider: str = "gemini"  # gemini (Google OpenAI-compatible endpoint)
    hermes_model: str = ""  # default per provider below
    rag_embed: bool = True  # dense layer of search_knowledge (Gemini embeddings); False = BM25 only
    rag_embed_model: str = "gemini-embedding-001"
    rag_embed_dims: int = 768
    rag_embed_max: int = 4000  # new chunks embedded per reindex (the rest wait for the next one)
    harness: str = "hermes"  # hermes (Hermes Agent over MCP) | pydantic (in-process pydantic-ai agent)
    opik_workspace: str = ""
    opik_project_name: str = "wellops"
    opik_url_override: str = ""
    otel_exporter_otlp_endpoint: str = ""

    @property
    def fallback_models(self) -> list[str]:
        listed = [m.strip() for m in self.model_fallbacks.split(",") if m.strip()]
        return listed or [self.model_orchestrator, self.model_worker]


settings = Settings()
