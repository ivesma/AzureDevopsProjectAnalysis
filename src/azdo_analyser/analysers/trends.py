"""Historical trend analysis and architectural drift detection.

Each run produces a compact *history record* (written by the JSON exporter to ``History/``). This
analyser loads the most recent previous record from the output folder and compares it with the
current run: metric deltas, new/resolved findings and added/removed dependency edges (drift).
"""

from __future__ import annotations

import json
from pathlib import Path

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_analyser

DRIFT_GRAPHS = ("pipeline-dependencies", "repository-dependencies", "service-connections", "template-usage",
                "variable-groups", "environments", "agent-pools")


def history_record(ctx: RunContext) -> dict:
    a = ctx.assessment
    metrics = ctx.analysis.get("metrics", {})
    return {
        "generated_at": a.generated_at.isoformat(),
        "organisation": a.organisation,
        "project": a.project,
        "risk_score": (ctx.analysis.get("risk") or {}).get("score"),
        "technical_debt": (ctx.analysis.get("technical_debt") or {}).get("score"),
        "severity_counts": a.severity_counts(),
        "inventory": metrics.get("inventory", {}),
        "activity": {k: v for k, v in (metrics.get("activity") or {}).items() if not isinstance(v, dict)},
        "findings": sorted(f.id for f in a.findings),
        "edges": {name: sorted(f"{s} -> {t}" for s, t in a.graphs[name].edge_set())
                  for name in DRIFT_GRAPHS if name in a.graphs},
    }


def load_previous(history_dir: Path, project: str) -> dict | None:
    if not history_dir.exists():
        return None
    for file in sorted(history_dir.glob("run-*.json"), reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("project") == project:
            return data
    return None


def compare(previous: dict, current: dict) -> dict:
    def deltas(section: str) -> dict:
        out = {}
        for key, value in (current.get(section) or {}).items():
            before = (previous.get(section) or {}).get(key)
            if isinstance(value, (int, float)) and isinstance(before, (int, float)) and value != before:
                out[key] = {"previous": before, "current": value, "delta": round(value - before, 3)}
        return out

    prev_f, cur_f = set(previous.get("findings", [])), set(current.get("findings", []))
    drift = {}
    for name in DRIFT_GRAPHS:
        before = set((previous.get("edges") or {}).get(name, []))
        after = set((current.get("edges") or {}).get(name, []))
        if before != after:
            drift[name] = {"added": sorted(after - before), "removed": sorted(before - after)}
    return {
        "previous_run": previous.get("generated_at"),
        "risk_score": {"previous": previous.get("risk_score"), "current": current.get("risk_score")},
        "technical_debt": {"previous": previous.get("technical_debt"), "current": current.get("technical_debt")},
        "inventory": deltas("inventory"),
        "activity": deltas("activity"),
        "new_findings": sorted(cur_f - prev_f),
        "resolved_findings": sorted(prev_f - cur_f),
        "drift": drift,
    }


@register_analyser("trends", order=70, depends_on=("risk",))
class TrendAnalyser(Analyser):
    """Compare with the previous run: metric trends, new/resolved findings and architectural drift."""

    def analyse(self, ctx: RunContext) -> None:
        record = history_record(ctx)
        ctx.analysis["history_record"] = record
        previous = load_previous(Path(ctx.settings.output_folder) / "History", ctx.assessment.project)
        ctx.analysis["trends"] = compare(previous, record) if previous else None
