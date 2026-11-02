"""Standalone interactive HTML report (single file; Mermaid loaded from a CDN)."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from azdo_analyser import __version__
from azdo_analyser.core.base import Exporter
from azdo_analyser.core.config import Settings
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_exporter
from azdo_analyser.exporters import tables
from azdo_analyser.visualisers import mermaid

SEVERITY_COLORS = {"CRITICAL": "#8f1d1d", "HIGH": "#d03b3b", "MEDIUM": "#ec835a", "LOW": "#fab219", "INFO": "#9a9890"}

# (section id, title, sheet, columns) - columns None = all
TABLE_SECTIONS = [
    ("repositories", "Repositories", "Repositories",
     ["Repository", "Default Branch", "Last Commit", "Days Since Commit", "Commits (window)", "Contributors (window)",
      "PRs Completed (window)", "Branch Policies", "Min Reviewers", "Build Validation", "Technologies", "URL"]),
    ("pipelines", "Pipelines", "Pipelines",
     ["Pipeline", "Type", "Folder", "Repository", "Triggers", "Variable Groups", "Templates", "Service Connections",
      "Agent Pools", "Environments", "Complexity Score", "Complexity", "Runs (window)", "Success Rate", "Last Result",
      "URL"]),
    ("releases", "Release Pipelines", "ReleasePipelines",
     ["Release Pipeline", "Stage", "Depends On", "Pre-deploy Approval", "Approvers", "Pre-deploy Gates",
      "Service Connections", "URL"]),
    ("environments", "Environments", "Environments", None),
    ("service-connections", "Service Connections", "ServiceConnections",
     ["Service Connection", "Type", "Auth Scheme", "Open Access", "Checks", "Last Used", "Consumers", "Unused", "URL"]),
    ("library", "Variable Groups", "VariableGroups", None),
    ("agent-pools", "Agent Pools", "AgentPools", None),
    ("boards", "Work Items", "WorkItems",
     ["ID", "Type", "Title", "State", "Assigned To", "Created", "Closed", "Lead Time (days)", "Cycle Time (days)"]),
    ("wiki", "Wiki Pages", "Wiki", None),
    ("tests", "Test Plans", "TestPlans", None),
    ("feeds", "Feeds & Packages", "Feeds", None),
    ("security", "Permissions", "Permissions", None),
]
DIAGRAMS_BY_SECTION = {
    "repositories": ["repository-dependencies"],
    "pipelines": ["pipeline-dependencies", "template-usage"],
    "environments": ["environments"],
    "service-connections": ["service-connections"],
    "library": ["variable-groups"],
    "agent-pools": ["agent-pools"],
    "boards": ["work-item-hierarchy"],
    "security": ["security-groups"],
}


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def hbar_svg(items: list[tuple[str, float, str]], width: int = 460, unit: str = "") -> Markup:
    """Horizontal bar chart: label column, thin bars anchored at the baseline, value labels."""
    if not items or not any(v for _, v, _ in items):
        return Markup('<p class="muted">No data.</p>')
    row, bar_h, label_w = 30, 14, 150
    plot_w = width - label_w - 60
    vmax = max(v for _, v, _ in items) or 1
    height = row * len(items) + 8
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" width="100%">']
    for i, (label, value, color) in enumerate(items):
        y = i * row + 8
        w = max(plot_w * value / vmax, 0 if value == 0 else 3)
        tip = escape(f"{label}: {fmt(value)}{unit}")
        parts.append(f'<text x="{label_w - 8}" y="{y + bar_h - 2}" text-anchor="end" class="axis-label">'
                     f'{escape(label[:24])}</text>')
        parts.append(f'<rect class="hit" x="{label_w}" y="{y - 6}" width="{plot_w + 50}" height="{row - 2}" '
                     f'fill="transparent" data-tip="{tip}"/>')
        if w:
            parts.append(f'<path d="M{label_w},{y} h{max(w - 4, 0):.1f} q4,0 4,4 v{bar_h - 8} q0,4 -4,4 '
                         f'h-{max(w - 4, 0):.1f} z" fill="{color}" pointer-events="none"/>')
        parts.append(f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h - 2}" class="value-label">'
                     f'{escape(fmt(value))}{unit}</text>')
    parts.append(f'<line x1="{label_w}" x2="{label_w}" y1="2" y2="{height - 2}" class="baseline"/></svg>')
    return Markup("".join(parts))


def column_svg(series: dict[str, float], title: str, width: int = 340, height: int = 150) -> Markup:
    """Weekly column chart for one measure (small multiple)."""
    if not series:
        return Markup(f'<figure class="mini"><figcaption>{escape(title)}</figcaption>'
                      '<p class="muted">No data in window.</p></figure>')
    keys = list(series)[-16:]
    vmax = max(series[k] for k in keys) or 1
    pad_l, pad_b, pad_t = 28, 22, 10
    plot_w, plot_h = width - pad_l - 6, height - pad_b - pad_t
    step = plot_w / len(keys)
    bar_w = max(min(step - 2, 18), 2)
    parts = [f'<figure class="mini"><figcaption>{escape(title)}</figcaption>'
             f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" width="100%">',
             f'<line x1="{pad_l}" x2="{width - 4}" y1="{pad_t}" y2="{pad_t}" class="grid"/>',
             f'<text x="{pad_l - 4}" y="{pad_t + 4}" text-anchor="end" class="axis-label">{fmt(vmax)}</text>',
             f'<text x="{pad_l - 4}" y="{pad_t + plot_h}" text-anchor="end" class="axis-label">0</text>']
    for i, key in enumerate(keys):
        value = series[key]
        h = plot_h * value / vmax
        x = pad_l + i * step + (step - bar_w) / 2
        y = pad_t + plot_h - h
        parts.append(f'<rect class="hit" x="{pad_l + i * step:.1f}" y="{pad_t}" width="{step:.1f}" height="{plot_h}" '
                     f'fill="transparent" data-tip="{escape(key)}: {fmt(value)}"/>')
        if h > 0:
            r = min(4, h, bar_w / 2)
            parts.append(f'<path d="M{x:.1f},{pad_t + plot_h} v-{h - r:.1f} q0,-{r} {r},-{r} h{bar_w - 2 * r:.1f} '
                         f'q{r},0 {r},{r} v{h - r:.1f} z" class="col" pointer-events="none"/>')
    parts.append(f'<line x1="{pad_l}" x2="{width - 4}" y1="{pad_t + plot_h}" y2="{pad_t + plot_h}" class="baseline"/>')
    parts.append(f'<text x="{pad_l}" y="{height - 6}" class="axis-label">{escape(keys[0])}</text>')
    parts.append(f'<text x="{width - 4}" y="{height - 6}" text-anchor="end" class="axis-label">{escape(keys[-1])}</text>')
    parts.append("</svg></figure>")
    return Markup("".join(parts))


def gauge(score: float | None, grade: str | None, label: str) -> dict:
    return {"label": label, "score": score if score is not None else 0, "grade": grade or "-"}


def build_context(ctx: RunContext) -> dict:
    a, an = ctx.assessment, ctx.analysis
    t = tables.build_all(ctx)
    metrics = an.get("metrics") or {}
    act, boards = metrics.get("activity") or {}, metrics.get("boards") or {}
    inv = metrics.get("inventory") or {}
    counts = a.severity_counts()

    def pct(v):
        return "n/a" if v is None else f"{v:.0%}"

    kpis = [
        ("Repositories", inv.get("repositories", 0), f"{act.get('active_repositories', 0)} with commits in window"),
        ("Pipelines", inv.get("pipelines", 0), f"{inv.get('yaml_pipelines', 0)} YAML · {inv.get('classic_pipelines', 0)} classic"),
        ("Build success", pct(act.get("build_success_rate")), f"{act.get('builds', 0)} runs"),
        ("Deployments / week", act.get("deployments_per_week", 0), f"{act.get('production_deployments', 0)} to production"),
        ("Commits / week", act.get("commits_per_week", 0), f"{act.get('active_contributors', 0)} contributors"),
        ("PR throughput / week", act.get("pr_throughput_per_week", 0),
         f"avg {act.get('avg_pr_completion_hours') or 'n/a'} h to complete"),
        ("Change failure rate", pct(act.get("change_failure_rate")), "production deployments"),
        ("Open work items", boards.get("open", 0), f"lead time median {(boards.get('lead_time_days') or {}).get('median') or 'n/a'} d"),
        ("Service connections", inv.get("service_connections", 0), f"{sum(1 for r in t['ServiceConnections'] if r['Unused'])} unused"),
        ("Est. pipeline cost / month", f"${(metrics.get('cost') or {}).get('estimated_parallel_job_cost_per_month', 0)}",
         "parallel jobs (estimate)"),
    ]
    severity_chart = hbar_svg([(s, counts[s], SEVERITY_COLORS[s]) for s in SEVERITY_COLORS])
    category_chart = hbar_svg([(k, v, "var(--series-1)") for k, v in (an.get("risk") or {}).get("by_category", {}).items()])
    activity_charts = [column_svg(act.get("commits_by_week") or {}, "Commits per week"),
                       column_svg(act.get("builds_by_week") or {}, "Pipeline runs per week"),
                       column_svg(act.get("deployments_by_week") or {}, "Deployments per week")]
    complexity = sorted(((k, v["score"], "var(--series-1)") for k, v in (an.get("complexity") or {}).items()),
                        key=lambda x: -x[1])[:12]
    debt_components = [(k.replace("_", " "), v, "var(--series-1)")
                       for k, v in ((an.get("technical_debt") or {}).get("components") or {}).items()]

    def diagram(name):
        g = a.graphs.get(name)
        if not g or g.is_empty:
            return None
        return {"id": name, "title": g.title, "description": g.description, "code": mermaid.render(g, max_nodes=150)}

    sections = []
    for sid, title, sheet, columns in TABLE_SECTIONS:
        rows = t.get(sheet, [])
        cols = columns or (list(rows[0].keys()) if rows else [])
        sections.append({
            "id": sid, "title": title,
            "columns": cols,
            "rows": [[fmt(r.get(c)) for c in cols] for r in rows[:2000]],
            "total": len(rows),
            "diagrams": [d for d in (diagram(n) for n in DIAGRAMS_BY_SECTION.get(sid, [])) if d],
        })
    pipeline_flows = [d for d in (diagram(g.name) for g in a.graphs.values() if g.category == "pipelines") if d]
    wiki_maps = [d for d in (diagram(g.name) for g in a.graphs.values() if g.category == "wiki") if d]
    findings = [{"severity": f.severity.value, "category": f.category, "resource_type": f.resource_type,
                 "resource": f.resource, "title": f.title, "description": f.description, "impact": f.impact,
                 "recommendation": f.recommendation, "remediation": f.remediation, "url": f.url,
                 "color": SEVERITY_COLORS[f.severity.value]} for f in a.sorted_findings()]
    summary = an.get("summary") or {}
    return {
        "title": f"{a.project} - Azure DevOps Assessment",
        "project": a.project, "organisation": a.organisation,
        "generated": a.generated_at.strftime("%Y-%m-%d %H:%M UTC"), "version": __version__,
        "window": ctx.settings.history_days,
        "risk": gauge((an.get("risk") or {}).get("score"), (an.get("risk") or {}).get("grade"), "Risk score"),
        "debt": gauge((an.get("technical_debt") or {}).get("score"), (an.get("technical_debt") or {}).get("grade"),
                      "Technical debt"),
        "counts": counts, "severity_colors": SEVERITY_COLORS,
        "kpis": kpis, "severity_chart": severity_chart, "category_chart": category_chart,
        "activity_charts": activity_charts, "complexity_chart": hbar_svg(complexity),
        "debt_chart": hbar_svg(debt_components),
        "summary_headline": summary.get("headline", ""),
        "summary_paragraphs": [summary["llm_text"]] if summary.get("llm_text") else summary.get("paragraphs", []),
        "summary_source": summary.get("source"),
        "recommendations": an.get("recommendations", []),
        "findings": findings,
        "categories": sorted({f["category"] for f in findings}),
        "sections": sections,
        "pipeline_flows": pipeline_flows,
        "wiki_maps": wiki_maps,
        "anti_patterns": t["AntiPatterns"],
        "trends": an.get("trends"),
        "coverage": t["Coverage"],
        "data_json": Markup(json.dumps({"counts": counts})),
    }


@register_exporter("html", order=50)
class HtmlExporter(Exporter):
    """HTML/index.html interactive report."""

    folder = "HTML"

    def enabled(self, settings: Settings) -> bool:
        return settings.generate_html

    def export(self, ctx: RunContext, output_dir: Path) -> list[Path]:
        env = Environment(loader=PackageLoader("azdo_analyser", "templates"),
                          autoescape=select_autoescape(["html", "j2"]))
        html = env.get_template("report.html.j2").render(**build_context(ctx))
        path = self.target(output_dir) / "index.html"
        path.write_text(html, encoding="utf-8")
        return [path]
