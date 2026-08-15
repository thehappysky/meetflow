from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEETFLOW_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    database_url: str = ""
    model_base_url: str = "https://api.openai.com/v1"
    model_api_key: str = ""
    model_name: str = ""
    model_api_profile: Literal["openai", "ollama", "ollama_json"] = "openai"
    model_context_window: int = 8192
    model_timeout: float = 60
    max_model_calls: int = 48
    max_tool_calls: int = 100
    max_agent_steps: int = 10
    max_output_tokens: int = 3000
    run_timeout: int = 900
    max_upload_mb: int = 100
    asr_model: str = ""
    asr_device: str = "cpu"
    start_worker: bool = True
    auth_enabled: bool = True
    report_font: str = ""

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir.resolve() / 'meetflow.db'}"

    @property
    def live_available(self) -> bool:
        return bool(self.model_name and (self.model_api_profile in {"ollama", "ollama_json"} or self.model_api_key))
