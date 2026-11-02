"""Runs collectors -> analysers -> exporters with progress reporting and fault isolation."""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from azdo_analyser.core.client import AzureDevOpsClient
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Assessment
from azdo_analyser.core.plugin import plugins

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, str], None]  # (phase, plugin, status)


class FatalCollectionError(RuntimeError):
    """A collector marked ``fatal`` failed (e.g. the project does not exist or access is denied)."""


@dataclass
class RunResult:
    assessment: Assessment
    outputs: dict[str, list[Path]] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


class Orchestrator:
    def __init__(self, settings: Settings, client: AzureDevOpsClient | None,
                 progress: ProgressCallback | None = None, now: datetime | None = None):
        self.settings = settings
        self.client = client
        self.progress = progress or (lambda *_: None)
        assessment = Assessment(organisation=settings.organisation_name, project=settings.project)
        self.ctx = RunContext(settings, client, assessment)
        if now:
            self.ctx.now = now
            assessment.generated_at = now

    def _step(self, phase: str, name: str, fn: Callable[[], object], result: RunResult) -> object:
        self.ctx.current_plugin = name
        self.progress(phase, name, "start")
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:  # isolate plugin failures
            log.debug("Plugin %s failed:\n%s", name, traceback.format_exc())
            result.failures[f"{phase}:{name}"] = f"{type(exc).__name__}: {exc}"
            self.ctx.record(phase, "error", f"{type(exc).__name__}: {exc}")
            self.progress(phase, name, "failed")
            return None
        finally:
            result.timings[f"{phase}:{name}"] = round(time.perf_counter() - started, 3)
        self.progress(phase, name, "done")
        return value

    def collect(self, result: RunResult) -> None:
        for spec in plugins("collector"):
            if not self.settings.collector_enabled(spec.name):
                self.ctx.current_plugin = spec.name
                self.ctx.record("collector", "skipped", "disabled by configuration")
                continue
            collector = spec.factory()
            self._step("collect", spec.name, lambda c=collector: c.run(self.ctx), result)
            if getattr(collector, "fatal", False) and f"collect:{spec.name}" in result.failures:
                raise FatalCollectionError(result.failures[f"collect:{spec.name}"])

    def analyse(self, result: RunResult) -> None:
        for spec in plugins("analyser"):
            analyser = spec.factory()
            self._step("analyse", spec.name, lambda a=analyser: a.analyse(self.ctx), result)

    def export(self, result: RunResult, output_dir: Path | None = None) -> None:
        out = Path(output_dir or self.settings.output_folder)
        out.mkdir(parents=True, exist_ok=True)
        for spec in plugins("exporter"):
            exporter = spec.factory()
            if not exporter.enabled(self.settings):
                continue
            files = self._step("export", spec.name, lambda e=exporter: e.export(self.ctx, out), result)
            if files:
                result.outputs[spec.name] = list(files)

    def run(self, collect: bool = True) -> RunResult:
        result = RunResult(self.ctx.assessment)
        if collect:
            self.collect(result)
        self.analyse(result)
        self.export(result)
        return result
