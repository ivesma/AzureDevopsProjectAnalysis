"""Plugin base classes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext


class Collector:
    """Gathers raw data from Azure DevOps and stores normalised records in the snapshot."""

    name = "collector"
    section: str = ""  # snapshot key this collector owns
    fatal = False  # when True a failure aborts the whole run

    def collect(self, ctx: RunContext) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(self, ctx: RunContext) -> None:
        result = self.collect(ctx)
        if self.section and result is not None:
            ctx.snapshot[self.section] = result


class Analyser:
    """Derives graphs, metrics, scores and findings from the snapshot."""

    name = "analyser"

    def analyse(self, ctx: RunContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class Exporter:
    """Writes assessment output to disk (or a remote target)."""

    name = "exporter"
    folder = ""

    def enabled(self, settings: Settings) -> bool:
        return True

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:  # pragma: no cover - abstract
        raise NotImplementedError

    def target(self, output_dir: Path) -> Path:
        path = output_dir / self.folder if self.folder else output_dir
        path.mkdir(parents=True, exist_ok=True)
        return path
