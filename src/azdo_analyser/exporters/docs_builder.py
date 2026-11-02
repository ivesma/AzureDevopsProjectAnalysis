"""Builds the documentation page tree shared by the Markdown and Azure DevOps Wiki exporters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Graph
from azdo_analyser.exporters import tables
from azdo_analyser.visualisers import mermaid

SEVERITY_BADGE = {"CRITICAL": "🟣 CRITICAL", "HIGH": "🔴 HIGH", "MEDIUM": "🟠 MEDIUM", "LOW": "🟡 LOW", "INFO": "⚪ INFO"}


@dataclass
class Page:
    title: str
    content: str
    children: list["Page"] = field(default_factory=list)

    def walk(self, parents: tuple[str, ...] = ()):
        yield parents, self
        for child in self.children:
            yield from child.walk(parents + (self.title,))


def page_title(text: str) -> str:
    """Wiki page names cannot contain / \\ # ? : * < > | or quotes."""
    cleaned = re.sub(r'[\\/#?:*<>|"\[\]]+', " ", str(text)).strip()
    return re.sub(r"\s+", " ", cleaned)[:200] or "Untitled"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✔" if value else "✘"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    text = str(value).replace("|", "\\|").replace("\n", "<br>")
    if text.startswith("http"):
        return f"[link]({text})"
    return text if len(text) <= 160 else text[:157] + "…"


def md_table(rows: list[dict], columns: list[str] | None = None, limit: int = 200) -> str:
    if not rows:
        return "_No data._\n"
    columns = columns or list(rows[0].keys())
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows[:limit]:
        values = []
        for col in columns:
            value = row.get(col)
            if col == "Severity" and value in SEVERITY_BADGE:
                value = SEVERITY_BADGE[value]
            values.append(_cell(value))
        lines.append("| " + " | ".join(values) + " |")
    if len(rows) > limit:
        lines.append(f"\n_{len(rows) - limit} more rows - see the Excel workbook._")
    return "\n".join(lines) + "\n"


class DocsBuilder:
    def __init__(self, ctx: RunContext, flavour: str = "markdown"):
        self.ctx = ctx
        self.flavour = flavour  # markdown -> ```mermaid ; azure -> ::: mermaid
        self.t = {name: builder(ctx) for name, builder, _ in tables.SHEETS}

    def diagram(self, graph: Graph | None, heading: str | None = None) -> str:
        if not graph or graph.is_empty:
            return ""
        title = f"### {heading or graph.title}\n\n" if heading is not False else ""
        desc = f"{graph.description}\n\n" if graph.description else ""
        return f"{title}{desc}{mermaid.fenced(graph, self.flavour, max_nodes=120)}\n\n"

    def graph(self, name: str) -> Graph | None:
        return self.ctx.assessment.graphs.get(name)

    def findings_for(self, resource_type: str | None = None, resource: str | None = None, category: str | None = None) -> str:
        rows = [r for r in self.t["RiskRegister"]
                if (resource_type is None or r["Resource Type"] == resource_type)
                and (resource is None or r["Resource"] == resource or r["Resource"].startswith(f"{resource} /"))
                and (category is None or r["Category"] == category)]
        return md_table(rows, ["Severity", "Title", "Resource", "Description", "Recommendation"])

    # ------------------------------------------------------------------ pages
    def build(self) -> Page:
        ctx = self.ctx
        root = Page(ctx.settings.wiki_root, self.overview())
        root.children = [
            Page("Executive Summary", self.executive_summary()),
            Page("Repositories", self.repositories()),
            Page("Pipelines", self.pipelines(), self.pipeline_pages()),
            Page("Release Pipelines", self.releases(), self.release_pages()),
            Page("Environments", self.environments()),
            Page("Service Connections", self.service_connections()),
            Page("Variables and Library", self.library()),
            Page("Agent Pools", self.agent_pools()),
            Page("Boards", self.boards()),
            Page("Wiki Health", self.wiki_health()),
            Page("Test Plans and Artifacts", self.tests_and_artifacts()),
            Page("Security", self.security()),
            Page("Risks", self.risks()),
            Page("Recommendations", self.recommendations()),
            Page("Trends and Drift", self.trends()),
            Page("Data Coverage", self.coverage()),
        ]
        return root

    def overview(self) -> str:
        a, an = self.ctx.assessment, self.ctx.analysis
        risk, debt = an.get("risk", {}), an.get("technical_debt", {})
        counts = a.severity_counts()
        summary = an.get("summary", {})
        kpis = [
            {"Metric": "Risk score", "Value": f"{risk.get('score')} / 100 (grade {risk.get('grade')})"},
            {"Metric": "Technical debt", "Value": f"{debt.get('score')} (grade {debt.get('grade')})"},
            {"Metric": "Findings", "Value": " · ".join(f"{k}: {v}" for k, v in counts.items() if v)},
        ] + [{"Metric": r["Item"].replace("Inventory - ", "").capitalize(), "Value": r["Value"]}
             for r in self.t["Summary"] if r["Item"].startswith("Inventory - ")]
        return (f"# {self.ctx.settings.wiki_root}: {a.project}\n\n"
                f"_Generated by azdo-analyser on {a.generated_at:%Y-%m-%d %H:%M} UTC for "
                f"{a.organisation} / {a.project}._\n\n"
                f"**{summary.get('headline', '')}**\n\n" + md_table(kpis) + "\n"
                + self.diagram(self.graph("pipeline-dependencies")))

    def executive_summary(self) -> str:
        s = self.ctx.analysis.get("summary", {})
        body = s.get("llm_text") or "\n\n".join(s.get("paragraphs", []))
        recs = self.t["Recommendations"][:10]
        return (f"# Executive Summary\n\n**{s.get('headline', '')}**\n\n{body}\n\n## Top recommendations\n\n"
                + md_table(recs, ["Priority", "Severity", "Title", "Occurrences", "Recommendation"]))

    def repositories(self) -> str:
        return ("# Repositories\n\n" + md_table(self.t["Repositories"], [
            "Repository", "Default Branch", "Last Commit", "Commits (window)", "Contributors (window)",
            "Branch Policies", "Min Reviewers", "Build Validation", "Technologies"])
                + "\n" + self.diagram(self.graph("repository-dependencies"))
                + "## Findings\n\n" + self.findings_for("repository"))

    def pipelines(self) -> str:
        return ("# Pipelines\n\n" + md_table(self.t["Pipelines"], [
            "Pipeline", "Type", "Repository", "Triggers", "Agent Pools", "Environments", "Complexity",
            "Runs (window)", "Success Rate", "Last Result"])
                + "\n" + self.diagram(self.graph("pipeline-dependencies"))
                + self.diagram(self.graph("template-usage"))
                + "## Templates\n\n" + md_table(self.t["Templates"])
                + "\n## YAML anti-patterns\n\n" + md_table(self.t["AntiPatterns"]))

    def pipeline_pages(self) -> list[Page]:
        pages = []
        effective = self.ctx.analysis.get("yaml", {}).get("pipelines", {})
        complexity = self.ctx.analysis.get("complexity", {})
        for p, row in zip(self.ctx.snapshot.get("pipelines", []), self.t["Pipelines"]):
            m = effective.get(str(p["id"])) or {}
            meta = [{"Property": k, "Value": v} for k, v in row.items()
                    if k not in ("Pipeline",) and v not in (None, "")]
            parts = [f"# {p['name']}\n\n", md_table(meta), "\n"]
            parts.append(self.diagram(self.graph(f"stage-flow-{p['id']}"), "Stage flow"))
            if m.get("stages"):
                stage_rows = [{"Stage": s["name"], "Depends On": ", ".join(s["depends_on"] or []) if s.get("depends_on")
                               is not None else "(previous)", "Jobs": ", ".join(
                                   f"{j['name']} ({j['kind']}{' → ' + str(j['environment']) if j.get('environment') else ''})"
                                   for j in s.get("jobs", [])), "Condition": s.get("condition")}
                              for s in m["stages"]]
                parts += ["## Stages\n\n", md_table(stage_rows), "\n"]
            if m.get("templates"):
                parts += ["## Templates used\n\n", "\n".join(f"- `{t}`" for t in m["templates"]), "\n\n"]
            if p.get("variables") or m.get("variables_inline"):
                vars_rows = [r for r in self.t["Variables"] if r["Owner"] == p["name"]]
                parts += ["## Variables\n\n", md_table(vars_rows, ["Variable", "Secret", "Source", "Value"]), "\n"]
            c = complexity.get(p["name"])
            if c:
                parts += [f"## Complexity\n\nScore **{c['score']}** ({c['band']}) - {c['stages']} stages, "
                          f"{c['jobs']} jobs, {c['steps']} steps, {c['templates']} templates.\n\n"]
            parts += ["## Findings\n\n", self.findings_for(None, p["name"])]
            pages.append(Page(page_title(p["name"]), "".join(parts)))
        return pages

    def releases(self) -> str:
        return "# Release Pipelines\n\n" + md_table(self.t["ReleasePipelines"], [
            "Release Pipeline", "Stage", "Depends On", "Pre-deploy Approval", "Approvers", "Pre-deploy Gates",
            "Service Connections"])

    def release_pages(self) -> list[Page]:
        pages = []
        for r in self.ctx.snapshot.get("release_pipelines", []):
            rows = [x for x in self.t["ReleasePipelines"] if x["Release Pipeline"] == r["name"]]
            content = (f"# {r['name']}\n\n" + self.diagram(self.graph(f"release-flow-{r['id']}"), "Release flow")
                       + md_table(rows, ["Stage", "Depends On", "Pre-deploy Approval", "Approvers", "Pre-deploy Gates",
                                         "Task Names", "Service Connections"])
                       + "\n## Findings\n\n" + self.findings_for(None, r["name"]))
            pages.append(Page(page_title(r["name"]), content))
        return pages

    def environments(self) -> str:
        return ("# Environments\n\n" + md_table(self.t["Environments"], [
            "Environment", "Production", "Checks", "Approvers", "Resources", "Deployments (sampled)",
            "Last Deployment", "Pipelines"])
                + "\n" + self.diagram(self.graph("environments")) + "## Findings\n\n" + self.findings_for("environment"))

    def service_connections(self) -> str:
        return ("# Service Connections\n\n" + md_table(self.t["ServiceConnections"], [
            "Service Connection", "Type", "Auth Scheme", "Open Access", "Checks", "Last Used", "Consumers", "Unused"])
                + "\n" + self.diagram(self.graph("service-connections"))
                + "## Findings\n\n" + self.findings_for("service connection"))

    def library(self) -> str:
        return ("# Variables and Library\n\n## Variable groups\n\n" + md_table(self.t["VariableGroups"], [
            "Variable Group", "Type", "Key Vault", "Variables", "Secrets", "Open Access", "Consumers", "Unused"])
                + "\n## Usage matrix\n\n" + md_table(self.t["VariableMatrix"])
                + "\n" + self.diagram(self.graph("variable-groups"))
                + "## Secrets inventory\n\n" + md_table([
                    {"Owner": s["owner"], "Type": s["owner_type"], "Secret": s["name"], "Store": s["store"]}
                    for s in self.ctx.analysis.get("secrets", [])])
                + "\n## Secure files\n\n" + md_table(self.t["SecureFiles"]))

    def agent_pools(self) -> str:
        return ("# Agent Pools\n\n" + md_table(self.t["AgentPools"], [
            "Agent Pool", "Hosting", "Agents", "Online", "Agent Versions", "Open Access", "Runs (window)", "Consumers"])
                + "\n## Agents\n\n" + md_table(self.t["Agents"]) + "\n" + self.diagram(self.graph("agent-pools")))

    def boards(self) -> str:
        m = (self.ctx.analysis.get("metrics") or {}).get("boards") or {}
        rows = [{"Type": k, "Total": v["total"], "Open": v["open"], "Lead time median (d)": v["lead_time_days"]["median"],
                 "Cycle time median (d)": v["cycle_time_days"]["median"]} for k, v in (m.get("per_type") or {}).items()]
        totals = (f"**{m.get('total', 0)}** work items analysed · open {m.get('open', 0)} · closed {m.get('closed', 0)} · "
                  f"throughput {m.get('throughput_per_week', 0)}/week · unassigned open {m.get('unassigned_open', 0)}\n\n")
        return ("# Boards\n\n" + totals + md_table(rows) + "\n"
                + self.diagram(self.graph("work-item-hierarchy"))
                + "## Open items by assignee\n\n"
                + md_table([{"Assignee": k, "Open": v} for k, v in (m.get("open_by_assignee") or {}).items()]))

    def wiki_health(self) -> str:
        out = ["# Wiki Health\n\n"]
        for w in self.ctx.analysis.get("wiki", []):
            out.append(f"## {w['wiki']}\n\n{w['page_count']} pages · "
                       f"{sum(len(v) for v in w['broken_links'].values())} broken links · {len(w['orphans'])} orphaned\n\n")
            out.append(self.diagram(self.graph(f"wiki-sitemap-{w['wiki']}"), "Site map"))
            broken = [{"Page": p, "Broken Link": l} for p, links in w["broken_links"].items() for l in links]
            out.append("### Broken links\n\n" + md_table(broken) + "\n")
            out.append("### Orphaned pages\n\n" + ("\n".join(f"- {p}" for p in w["orphans"]) or "_None._") + "\n\n")
        return "".join(out) if len(out) > 1 else "# Wiki Health\n\n_No wikis found._\n"

    def tests_and_artifacts(self) -> str:
        return ("# Test Plans and Artifacts\n\n## Test plans\n\n" + md_table(self.t["TestPlans"])
                + "\n## Feeds and packages\n\n" + md_table(self.t["Feeds"]))

    def security(self) -> str:
        sec = self.ctx.snapshot.get("security") or {}
        groups = [{"Group": g["display_name"], "Members": g["member_count"], "Custom": g.get("is_custom")}
                  for g in sec.get("groups", [])]
        return ("# Security\n\n"
                f"**Project administrators:** {', '.join(sec.get('project_admins', [])) or 'n/a'}\n\n"
                f"**Service accounts / build identities:** "
                f"{', '.join(s['name'] for s in sec.get('service_accounts', [])) or 'n/a'}\n\n"
                "## Groups\n\n" + md_table(groups) + "\n"
                + self.diagram(self.graph("security-groups"))
                + "## Explicit permissions\n\n" + md_table(self.t["Permissions"])
                + "\n## Security findings\n\n" + self.findings_for(category="Security"))

    def risks(self) -> str:
        risk = self.ctx.analysis.get("risk", {})
        cats = [{"Category": k, "Weighted points": v} for k, v in (risk.get("by_category") or {}).items()]
        return (f"# Risks\n\nRisk score **{risk.get('score')}** / 100 (grade **{risk.get('grade')}**).\n\n"
                + md_table(cats) + "\n## Risk register\n\n"
                + md_table(self.t["RiskRegister"], ["Severity", "Category", "Resource", "Title", "Impact", "Recommendation"],
                           limit=400))

    def recommendations(self) -> str:
        out = ["# Recommendations\n\n"]
        for r in self.ctx.analysis.get("recommendations", []):
            steps = "\n".join(f"{i}. {s}" for i, s in enumerate(r["remediation"], 1))
            out.append(f"## {SEVERITY_BADGE.get(r['severity'], r['severity'])} - {r['title']}\n\n"
                       f"**Category:** {r['category']} · **Occurrences:** {r['occurrences']}\n\n"
                       f"**Impact:** {r['impact']}\n\n**Recommendation:** {r['recommendation']}\n\n"
                       + (f"**Remediation steps:**\n\n{steps}\n\n" if steps else "")
                       + f"**Affected:** {', '.join(r['resources'][:20])}\n\n")
        return "".join(out)

    def trends(self) -> str:
        tr = self.ctx.analysis.get("trends")
        if not tr:
            return "# Trends and Drift\n\n_No previous run found in the output folder - trends appear from the second run._\n"
        out = [f"# Trends and Drift\n\nCompared with run of {tr['previous_run']}.\n\n",
               f"- Risk score: {tr['risk_score']['previous']} → {tr['risk_score']['current']}\n",
               f"- Technical debt: {tr['technical_debt']['previous']} → {tr['technical_debt']['current']}\n",
               f"- New findings: {len(tr['new_findings'])} · resolved: {len(tr['resolved_findings'])}\n\n"]
        rows = [{"Metric": f"{sec}.{k}", "Previous": v["previous"], "Current": v["current"], "Delta": v["delta"]}
                for sec in ("inventory", "activity") for k, v in tr[sec].items()]
        out.append("## Metric changes\n\n" + md_table(rows) + "\n## Architectural drift\n\n")
        for name, change in tr["drift"].items():
            out.append(f"### {name}\n\n" + "".join(f"- ➕ {e}\n" for e in change["added"])
                       + "".join(f"- ➖ {e}\n" for e in change["removed"]) + "\n")
        if not tr["drift"]:
            out.append("_No dependency changes detected._\n")
        return "".join(out)

    def coverage(self) -> str:
        return ("# Data Coverage\n\nAreas that could not be collected (missing permissions, disabled features or "
                "errors). Grant the analysing identity access to fill the gaps.\n\n" + md_table(self.t["Coverage"]))
