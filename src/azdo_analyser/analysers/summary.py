"""Executive summary: deterministic narrative, optionally rewritten by Claude (``llm_summary: true``)."""

from __future__ import annotations

import json
import logging

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_analyser

log = logging.getLogger(__name__)

LLM_SYSTEM = (
    "You are a principal platform engineer writing the executive summary of an Azure DevOps project "
    "assessment for engineering leadership. Use only the facts in the JSON you are given; do not invent "
    "numbers. Write about 300 words of plain Markdown: one short paragraph on overall posture, then the "
    "most important risks and what to do first, then notable strengths. No headings."
)


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def deterministic_summary(ctx: RunContext) -> dict:
    a = ctx.assessment
    inv = ctx.analysis.get("metrics", {}).get("inventory", {})
    act = ctx.analysis.get("metrics", {}).get("activity", {})
    risk = ctx.analysis.get("risk", {})
    debt = ctx.analysis.get("technical_debt", {})
    counts = a.severity_counts()
    recs = ctx.analysis.get("recommendations", [])[:5]
    repos = ctx.snapshot.get("repositories", [])
    protected = sum(1 for r in repos if r["default_branch_policies"]["count"] and not r.get("is_empty"))
    active_repos = sum(1 for r in repos if not r.get("is_empty") and not r.get("is_disabled"))

    headline = (f"Risk score {risk.get('score', 0)}/100 (grade {risk.get('grade', '?')}) with "
                f"{counts['CRITICAL'] + counts['HIGH']} high-severity findings.")
    paragraphs = [
        f"The {a.project} project in {a.organisation} contains {inv.get('repositories', 0)} repositories, "
        f"{inv.get('pipelines', 0)} pipelines ({inv.get('yaml_pipelines', 0)} YAML, {inv.get('classic_pipelines', 0)} "
        f"classic), {inv.get('release_pipelines', 0)} classic release pipelines, {inv.get('environments', 0)} "
        f"environments and {inv.get('service_connections', 0)} service connections.",
        f"Over the last {ctx.settings.history_days} days there were {act.get('commits', 0)} commits by "
        f"{act.get('active_contributors', 0)} contributors, {act.get('builds', 0)} pipeline runs "
        f"(success rate {_pct(act.get('build_success_rate'))}) and {act.get('deployments', 0)} deployments "
        f"({act.get('production_deployments', 0)} to production).",
        f"The assessment raised {len(a.findings)} findings: {counts['HIGH']} high, {counts['MEDIUM']} medium, "
        f"{counts['LOW']} low. {protected} of {active_repos} active repositories protect their default branch. "
        f"Technical debt score is {debt.get('score', 0)} (grade {debt.get('grade', '?')}).",
    ]
    if recs:
        paragraphs.append("Top priorities: " + "; ".join(
            f"{r['title']} ({r['severity']}, {r['occurrences']}x)" for r in recs) + ".")
    return {"headline": headline, "paragraphs": paragraphs, "source": "deterministic"}


def summary_facts(ctx: RunContext) -> dict:
    metrics = ctx.analysis.get("metrics", {})
    return {
        "project": ctx.assessment.project,
        "risk": ctx.analysis.get("risk"),
        "technical_debt": ctx.analysis.get("technical_debt"),
        "severity_counts": ctx.assessment.severity_counts(),
        "inventory": metrics.get("inventory"),
        "activity": {k: v for k, v in (metrics.get("activity") or {}).items() if not isinstance(v, dict)},
        "boards": {k: v for k, v in (metrics.get("boards") or {}).items() if not isinstance(v, dict)},
        "top_recommendations": [
            {k: r[k] for k in ("title", "severity", "category", "occurrences", "impact", "recommendation")}
            for r in ctx.analysis.get("recommendations", [])[:10]
        ],
        "trends": ctx.analysis.get("trends"),
    }


def llm_summary(ctx: RunContext) -> str | None:
    try:
        import anthropic
    except ImportError:
        ctx.record("llm-summary", "skipped", "anthropic package not installed (pip install azdo-analyser[llm])")
        return None
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=ctx.settings.llm_model,
            max_tokens=4000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(summary_facts(ctx), default=str)}],
        )
    except Exception as exc:  # network, auth, SDK version - fall back to the deterministic summary
        ctx.record("llm-summary", "error", f"{type(exc).__name__}: {exc}")
        return None
    if response.stop_reason == "refusal":
        ctx.record("llm-summary", "error", "model declined the request")
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None


@register_analyser("summary", order=90, depends_on=("risk", "trends"))
class SummaryAnalyser(Analyser):
    """Executive summary narrative (optionally LLM-generated)."""

    def analyse(self, ctx: RunContext) -> None:
        summary = deterministic_summary(ctx)
        if ctx.settings.llm_summary:
            text = llm_summary(ctx)
            if text:
                summary["llm_text"] = text
                summary["source"] = f"llm:{ctx.settings.llm_model}"
        ctx.analysis["summary"] = summary
