"""Run context handed to every plugin."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from azdo_analyser.core.client import AzDoError, AzureDevOpsClient, NotFound, PermissionDenied, seg
from azdo_analyser.core.config import Settings
from azdo_analyser.core.models import Assessment, CoverageEntry

T = TypeVar("T")

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    settings: Settings
    client: AzureDevOpsClient | None
    assessment: Assessment
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_plugin: str = ""

    @property
    def project(self) -> str:
        """URL-safe project segment."""
        return seg(self.settings.project)

    @property
    def snapshot(self) -> dict[str, Any]:
        return self.assessment.snapshot

    @property
    def analysis(self) -> dict[str, Any]:
        return self.assessment.analysis

    def record(self, area: str, status: str, message: str = "") -> None:
        self.assessment.coverage.append(CoverageEntry(self.current_plugin, area, status, message))

    def safe(self, area: str, fn: Callable[[], T], default: T, record_ok: bool = False) -> T:
        """Run an API call, recording permission/feature gaps instead of failing the run."""
        try:
            result = fn()
        except PermissionDenied as exc:
            self.record(area, "denied", str(exc))
            log.info("[%s] %s: permission denied", self.current_plugin, area)
            return default
        except NotFound as exc:
            self.record(area, "not_found", str(exc))
            return default
        except AzDoError as exc:
            self.record(area, "error", str(exc))
            log.warning("[%s] %s: %s", self.current_plugin, area, exc)
            return default
        if record_ok:
            self.record(area, "ok")
        return result
