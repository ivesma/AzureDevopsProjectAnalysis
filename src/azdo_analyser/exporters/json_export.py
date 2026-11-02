"""Full JSON export plus the per-run history record used for trend analysis."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from azdo_analyser import __version__
from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def export_document(ctx: RunContext) -> dict:
    a = ctx.assessment
    settings = {k: v for k, v in vars(ctx.settings).items() if k != "pat"}
    return {
        "metadata": {
            "tool": "azdo-analyser",
            "version": __version__,
            "organisation": a.organisation,
            "project": a.project,
            "generated_at": a.generated_at.isoformat(),
            "settings": settings,
        },
        "snapshot": a.snapshot,
        "analysis": {k: v for k, v in a.analysis.items() if k != "history_record"},
        "findings": [f.to_dict() for f in a.sorted_findings()],
        "graphs": {name: g.to_dict() for name, g in a.graphs.items()},
        "coverage": [asdict(c) for c in a.coverage],
    }


@register_exporter("json", order=10)
class JsonExporter(Exporter):
    """Json/full-export.json and History/run-<timestamp>.json."""

    folder = "Json"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_json

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        target = self.target(output_dir)
        path = target / "full-export.json"
        path.write_text(json.dumps(export_document(ctx), indent=2, default=to_jsonable), encoding="utf-8")
        files = [path]
        record = ctx.analysis.get("history_record")
        if record:
            history = output_dir / "History"
            history.mkdir(parents=True, exist_ok=True)
            stamp = ctx.assessment.generated_at.strftime("%Y%m%dT%H%M%SZ")
            hpath = history / f"run-{stamp}.json"
            hpath.write_text(json.dumps(record, indent=2, default=to_jsonable), encoding="utf-8")
            files.append(hpath)
        return files
