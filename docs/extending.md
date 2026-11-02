# Extending the platform

## A new collector

```python
from azdo_analyser.core.base import Collector
from azdo_analyser.core.plugin import register_collector


@register_collector("dashboards", order=140, depends_on=("project",))
class DashboardCollector(Collector):
    """Team dashboards."""

    section = "dashboards"          # snapshot key; the return value of collect() is stored there

    def collect(self, ctx):
        team = ctx.snapshot["project"]["default_team"]
        return ctx.safe(            # records denied / not-found / errors in Coverage instead of failing
            "dashboards",
            lambda: ctx.client.get_all(f"{ctx.project}/{team}/_apis/dashboard/dashboards",
                                       api_version="7.1-preview.3"),
            [],
        )
```

`ctx.client` offers `get`, `post`, `put`, `get_all` (continuation tokens), `get_skip_paged` (`$top/$skip`) and a
`service=` argument for `vsrm`, `vssps`, `feeds`, `vsaex` and `auditservice` hosts. Return plain, JSON-serialisable
data.

## A new risk rule

```python
from azdo_analyser.analysers.risk_engine import finding, rule
from azdo_analyser.core.models import Severity


@rule("XX001")
def dashboards_without_owner(ctx):
    for d in ctx.snapshot.get("dashboards", []):
        if not d.get("ownerId"):
            yield finding("XX001", Severity.LOW, "Governance", "dashboard", d["name"],
                          "Dashboard without owner", "No owner is set.", "Stale dashboards mislead.",
                          "Assign an owner.", ["Dashboard > Settings > Owner"])
```

Rules are picked up when their module is imported; findings automatically flow into the risk score, risk
register, recommendations, Excel, HTML and wiki.

## A new graph

Create a `Graph`, add nodes/edges and call `ctx.assessment.add_graph(graph)` from an analyser. Every graph is
rendered to Mermaid, draw.io and DOT, and appears in the HTML report and docs when referenced by a section.

```python
from azdo_analyser.core.models import Graph

g = Graph("dashboards", "Dashboards by team", "boards")
g.add_node("team:x", "Team X", "group")
g.add_node("dash:1", "Sprint board", "page")
g.add_edge("team:x", "dash:1", "owns")
ctx.assessment.add_graph(g)
```

## A new exporter

```python
from azdo_analyser.core.base import Exporter
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.exporters import tables


@register_exporter("csv", order=80)
class CsvExporter(Exporter):
    folder = "Csv"

    def export(self, ctx, output_dir):
        import pandas as pd

        files = []
        for name, builder, _ in tables.SHEETS:
            path = self.target(output_dir) / f"{name}.csv"
            pd.DataFrame(builder(ctx)).to_csv(path, index=False)
            files.append(path)
        return files
```

## Packaging third-party plugins

Expose a module through the `azdo_analyser.plugins` entry point; importing it (or calling it, if it is a
function) must register the plugins:

```toml
[project.entry-points."azdo_analyser.plugins"]
dashboards = "my_package.azdo_plugins"
```
