from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = CONFIG_DIR.parent


class ServerConfig(BaseModel):
    agent_backend_port: int = 8000
    alphagenome_svc_port: int = 8001
    cors_origins: list[str] = Field(default_factory=list)


class LimitsConfig(BaseModel):
    max_sequence_length: int = 131072
    max_upload_bytes: int = 10 * 1024 * 1024
    session_ttl_seconds: int = 3600


class PathsConfig(BaseModel):
    genome_fasta: Path
    genome_fai: Path
    alphagenome_weights: Path
    track_metadata_tsv: Path
    upload_dir: Path
    session_store_dir: Path
    prompts_dir: Path

    def resolve(self, root: Path) -> "PathsConfig":
        return PathsConfig(
            **{k: (root / v).resolve() if not v.is_absolute() else v for k, v in self.model_dump().items()}
        )


class HFModelEntry(BaseModel):
    repo_id: str
    filename: str
    revision: str = "main"


class ModelsConfig(BaseModel):
    huggingface: dict[str, HFModelEntry]
    ollama: dict[str, str]
    organism_index: dict[str, int]


class HuggingFaceConfig(BaseModel):
    cache_dir: Path
    token_env_var: str = "HUGGINGFACE_HUB_TOKEN"
    offline: bool = False


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "gemma:2b"
    temperature: float = 0.1
    max_tokens: int = 1024
    request_timeout_seconds: int = 120


class AlphaGenomeConfig(BaseModel):
    service_url: str = "http://localhost:8001"
    default_heads: list[str] = Field(default_factory=lambda: ["rna_seq", "chip_tf", "atac"])
    default_resolution: Literal["1bp", "128bp"] = "128bp"
    max_input_length: int = 131072
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    batch_size: int = 1
    dtype: Literal["float16", "float32", "bfloat16"] = "float16"


class EnsemblConfig(BaseModel):
    rest_base_url: str = "https://rest.ensembl.org"
    assembly: str = "GRCh38"
    request_timeout_seconds: int = 15
    rate_limit_per_second: int = 10


class AgentConfig(BaseModel):
    max_steps: int = 6
    agent_type: Literal["tool_calling", "code"] = "tool_calling"
    retry_on_parse_error: int = 2
    verbose: bool = True
    memory_turns: int = 4
    memory_max_chars: int = 1200


class ToolToggle(BaseModel):
    enabled: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIGEMMA_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_env: Literal["dev", "prod"] = "dev"
    server: ServerConfig = Field(default_factory=ServerConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    paths: PathsConfig
    models: ModelsConfig
    huggingface: HuggingFaceConfig
    ollama: OllamaConfig
    alphagenome: AlphaGenomeConfig
    ensembl: EnsemblConfig
    agent: AgentConfig
    tools: dict[str, ToolToggle]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    env = os.environ.get("APP_ENV", "dev")

    base = _read_yaml(CONFIG_DIR / "settings.yaml")
    overlay = _read_yaml(CONFIG_DIR / f"settings.{env}.yaml")
    merged = _deep_merge(base, overlay)

    merged["paths"] = _read_yaml(CONFIG_DIR / "paths.yaml")
    merged["models"] = _read_yaml(CONFIG_DIR / "models.yaml")
    merged["huggingface"] = _read_yaml(CONFIG_DIR / "huggingface.yaml")
    merged["ollama"] = _read_yaml(CONFIG_DIR / "ollama.yaml")
    merged["alphagenome"] = _read_yaml(CONFIG_DIR / "alphagenome.yaml")
    merged["ensembl"] = _read_yaml(CONFIG_DIR / "ensembl.yaml")
    merged["agent"] = _read_yaml(CONFIG_DIR / "agent.yaml")
    merged["tools"] = _read_yaml(CONFIG_DIR / "tools.yaml")
    merged["app_env"] = env

    settings = Settings(**merged)
    settings.paths = settings.paths.resolve(PROJECT_ROOT)
    settings.huggingface.cache_dir = Path(os.path.expanduser(str(settings.huggingface.cache_dir)))
    return settings
