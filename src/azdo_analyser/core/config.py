"""Configuration loading (YAML file + environment + CLI overrides)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

PAT_ENV_VARS = ("AZDO_PAT", "AZURE_DEVOPS_EXT_PAT", "SYSTEM_ACCESSTOKEN")


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


@dataclass
class Settings:
    organisation: str = ""
    project: str = ""
    pat: str | None = None
    auth: str = "pat"  # pat | azcli | managed_identity
    output_folder: Path = Path("./assessment")
    api_version: str = "7.1"

    # outputs
    generate_excel: bool = True
    generate_html: bool = True
    generate_mermaid: bool = True
    generate_json: bool = True
    generate_markdown: bool = True
    generate_drawio: bool = True
    generate_powerbi: bool = False
    wiki_publish: bool = False
    wiki_name: str | None = None
    wiki_root: str = "Project Assessment"

    # collection scope
    collectors: list[str] | None = None
    skip_collectors: list[str] = field(default_factory=list)
    history_days: int = 90
    stale_days: int = 180
    max_commits_per_repo: int = 2000
    max_pull_requests_per_repo: int = 500
    max_builds: int = 5000
    max_work_items: int = 5000
    max_yaml_files_per_repo: int = 300
    max_wiki_pages: int = 500
    expand_yaml_via_preview: bool = False
    scan_repository_yaml: bool = True

    # analysis tuning
    production_pattern: str = r"(?i)(^|[^a-z])(prod|production|prd|live)([^a-z]|$)"
    max_project_admins: int = 5
    min_build_success_rate: float = 0.7
    hosted_minute_cost: float = 0.008
    hosted_parallel_job_cost: float = 40.0
    self_hosted_parallel_job_cost: float = 15.0

    # transport
    timeout: int = 60
    max_retries: int = 5
    verify_ssl: bool = True

    # optional LLM executive summary
    llm_summary: bool = False
    llm_model: str = "claude-opus-5-5"

    @property
    def organisation_url(self) -> str:
        return normalise_org_url(self.organisation)

    @property
    def organisation_name(self) -> str:
        return org_name_from_url(self.organisation_url)

    def validate(self) -> None:
        if not self.organisation:
            raise ConfigError("'organisation' is required (e.g. https://dev.azure.com/company)")
        if not self.project:
            raise ConfigError("'project' is required")
        if self.auth not in {"pat", "azcli", "managed_identity"}:
            raise ConfigError(f"Unsupported auth method '{self.auth}'")
        if self.auth == "pat" and not self.pat:
            raise ConfigError(
                "A PAT is required for auth=pat. Provide --pat, 'pat:' in config or one of "
                + ", ".join(PAT_ENV_VARS)
            )
        try:
            re.compile(self.production_pattern)
        except re.error as exc:
            raise ConfigError(f"Invalid production_pattern: {exc}") from exc

    def collector_enabled(self, name: str) -> bool:
        if name in self.skip_collectors:
            return False
        return self.collectors is None or name in self.collectors

    def is_production(self, name: str | None) -> bool:
        if not name:
            return False
        # Split CamelCase so 'DeployProd' / 'ApplyProduction' match word-bounded patterns.
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
        return re.search(self.production_pattern, text) is not None


def normalise_org_url(org: str) -> str:
    """Accept 'company', 'https://dev.azure.com/company/' or 'company.visualstudio.com'."""
    org = (org or "").strip().rstrip("/")
    if not org:
        return ""
    if not re.match(r"^https?://", org):
        if "." in org:
            return f"https://{org}"
        return f"https://dev.azure.com/{org}"
    parsed = urlparse(org)
    if parsed.netloc.lower() == "dev.azure.com":
        name = parsed.path.strip("/").split("/")[0]
        return f"https://dev.azure.com/{name}"
    return org


def org_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host == "dev.azure.com":
        return parsed.path.strip("/").split("/")[0]
    if host.endswith(".visualstudio.com"):
        return host.split(".")[0]
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else host


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


_ALIASES = {"organization": "organisation", "org": "organisation", "output": "output_folder"}


def _coerce(name: str, value: Any) -> Any:
    default = getattr(Settings(), name)
    if value is None:
        return None
    if name == "output_folder":
        return Path(value)
    if name in {"collectors", "skip_collectors"}:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return list(value)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def load_settings(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    """Build Settings from an optional YAML file, then apply non-None overrides."""
    raw: dict[str, Any] = {}
    if path:
        file = Path(path)
        if not file.exists():
            raise ConfigError(f"Config file not found: {file}")
        loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("Config file must contain a mapping at the top level")
        raw.update(_expand_env(loaded))
    for key, value in (overrides or {}).items():
        if value is not None:
            raw[key] = value

    valid = {f.name for f in fields(Settings)}
    settings = Settings()
    unknown = []
    for key, value in raw.items():
        name = _ALIASES.get(key, key)
        if name not in valid:
            unknown.append(key)
            continue
        setattr(settings, name, _coerce(name, value))
    if unknown:
        raise ConfigError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")

    if not settings.pat and settings.auth == "pat":
        for var in PAT_ENV_VARS:
            if os.environ.get(var):
                settings.pat = os.environ[var]
                break
    return settings
