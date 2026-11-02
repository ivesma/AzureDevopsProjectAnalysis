"""Power BI export: star-schema CSV files (one per table) with a model description.

Load the folder in Power BI Desktop via *Get data > Folder* (or Text/CSV per file). Relationships are
described in ``model.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.exporters import tables

FACT_TABLES = {
    "fact_findings": tables.risk_register,
    "fact_work_items": tables.work_items,
    "fact_service_connection_usage": tables.service_connection_usage,
    "fact_pipeline_dependencies": tables.pipeline_dependencies,
}
DIM_TABLES = {
    "dim_repositories": tables.repositories,
    "dim_pipelines": tables.pipelines,
    "dim_service_connections": tables.service_connections,
    "dim_environments": tables.environments,
    "dim_variable_groups": tables.variable_groups,
    "dim_agent_pools": tables.agent_pools,
    "metrics": tables.metrics,
}
RELATIONSHIPS = [
    {"from": "fact_service_connection_usage[Service Connection]", "to": "dim_service_connections[Service Connection]"},
    {"from": "fact_pipeline_dependencies[Target]", "to": "dim_pipelines[Pipeline]"},
    {"from": "dim_pipelines[Repository]", "to": "dim_repositories[Repository]"},
]


@register_exporter("powerbi", order=70)
class PowerBiExporter(Exporter):
    """PowerBI/*.csv star schema + model.json (enable with generate_powerbi)."""

    folder = "PowerBI"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_powerbi

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        target = self.target(output_dir)
        files = []
        stamp = ctx.assessment.generated_at.replace(tzinfo=None)
        for name, builder in {**FACT_TABLES, **DIM_TABLES}.items():
            df = pd.DataFrame(builder(ctx))
            df.insert(0, "Project", ctx.assessment.project)
            df.insert(1, "Snapshot", stamp)
            path = target / f"{name}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            files.append(path)
        model = {"tables": list(FACT_TABLES) + list(DIM_TABLES), "relationships": RELATIONSHIPS,
                 "notes": "Append multiple runs into the same folder history to build trend reports."}
        (target / "model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
        files.append(target / "model.json")
        return files
