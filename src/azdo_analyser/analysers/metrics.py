"""Activity (DORA-style), boards flow, testing and cost metrics."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_analyser
from azdo_analyser.core.utils import days_between, parse_date, summarise

CLOSED_CATEGORIES = {"Completed", "Removed"}


def peak_concurrency(intervals: list[tuple]) -> int:
    events = []
    for start, end in intervals:
        if start and end and end > start:
            events.append((start, 1))
            events.append((end, -1))
    peak = current = 0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        current += delta
        peak = max(peak, current)
    return peak


def is_hosted_build(build: dict) -> bool:
    if build.get("pool_hosted") is not None:
        return bool(build["pool_hosted"])
    name = (build.get("pool") or "").lower()
    return name.startswith("azure pipelines") or name.startswith("hosted")


@register_analyser("metrics", order=40, depends_on=("dependencies",))
class MetricsAnalyser(Analyser):
    """Deployment & commit frequency, PR throughput, build success, boards flow, tests and cost estimate."""

    def analyse(self, ctx: RunContext) -> None:
        weeks = max(ctx.settings.history_days / 7, 1)
        ctx.analysis["metrics"] = {
            "window_days": ctx.settings.history_days,
            "activity": self._activity(ctx, weeks),
            "boards": self._boards(ctx, weeks),
            "tests": self._tests(ctx),
            "cost": self._cost(ctx),
            "inventory": self._inventory(ctx),
        }

    @staticmethod
    def _inventory(ctx: RunContext) -> dict:
        s = ctx.snapshot
        pipelines = s.get("pipelines", [])
        return {
            "repositories": len(s.get("repositories", [])),
            "pipelines": len(pipelines),
            "yaml_pipelines": sum(1 for p in pipelines if p["type"] == "yaml"),
            "classic_pipelines": sum(1 for p in pipelines if p["type"] == "classic"),
            "release_pipelines": len(s.get("release_pipelines", [])),
            "environments": len(s.get("environments", [])),
            "service_connections": len(s.get("service_connections", [])),
            "variable_groups": len(s.get("variable_groups", [])),
            "secure_files": len(s.get("secure_files", [])),
            "agent_pools": len(s.get("agent_pools", [])),
            "self_hosted_agents": sum(len(p.get("agents", [])) for p in s.get("agent_pools", [])),
            "teams": len((s.get("project") or {}).get("teams", [])),
            "work_items": len((s.get("boards") or {}).get("work_items", [])),
            "wiki_pages": sum(len(w.get("pages", [])) for w in (s.get("wiki") or {}).get("wikis", [])),
            "test_plans": len((s.get("test_plans") or {}).get("plans", [])),
            "feeds": len(s.get("feeds", [])),
            "packages": sum(f.get("package_count", 0) for f in s.get("feeds", [])),
            "yaml_files": len(s.get("yaml_files", {})),
            "templates": len(ctx.analysis.get("yaml", {}).get("templates", {})),
        }

    @staticmethod
    def _activity(ctx: RunContext, weeks: float) -> dict:
        s = ctx.snapshot
        is_prod = ctx.settings.is_production
        repos = s.get("repositories", [])
        commits = sum(r.get("commits_in_window", 0) for r in repos)
        contributors = {c["email"] for r in repos for c in r.get("contributors", [])}
        prs_completed = sum((r.get("pull_requests") or {}).get("completed_in_window", 0) for r in repos)
        prs_created = sum((r.get("pull_requests") or {}).get("created_in_window", 0) for r in repos)
        pr_hours = [(r.get("pull_requests") or {}).get("avg_completion_hours") for r in repos]
        builds = s.get("builds", [])
        finished = [b for b in builds if b.get("result")]
        succeeded = sum(1 for b in finished if b["result"] == "succeeded")

        deployments = []
        window_start = ctx.now - timedelta(days=ctx.settings.history_days)
        for env in s.get("environments", []):
            for d in env.get("deployments", []):
                start = parse_date(d.get("start_time"))
                if start and start >= window_start:
                    deployments.append({"target": env["name"], "result": d.get("result"), "prod": is_prod(env["name"]),
                                        "when": start, "source": "environment"})
        for d in s.get("release_deployments", []):
            start = parse_date(d.get("started_on"))
            if start and start >= window_start:
                deployments.append({"target": d.get("stage"), "result": d.get("status"), "prod": is_prod(d.get("stage")),
                                    "when": start, "source": "release"})
        prod = [d for d in deployments if d["prod"]]
        prod_failed = sum(1 for d in prod if str(d["result"]).lower() in ("failed", "partiallysucceeded", "rejected"))
        by_week = Counter(f"{d['when'].isocalendar()[0]}-W{d['when'].isocalendar()[1]:02d}" for d in deployments)
        builds_by_week = Counter()
        for b in builds:
            dt = parse_date(b.get("queue_time"))
            if dt:
                builds_by_week[f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"] += 1
        commits_by_week: Counter[str] = Counter()
        for r in repos:
            commits_by_week.update(r.get("commits_by_week", {}))
        valid_hours = [h for h in pr_hours if h is not None]
        return {
            "commits": commits,
            "commits_per_week": round(commits / weeks, 1),
            "active_contributors": len(contributors),
            "active_repositories": sum(1 for r in repos if r.get("commits_in_window")),
            "prs_created": prs_created,
            "prs_completed": prs_completed,
            "pr_throughput_per_week": round(prs_completed / weeks, 1),
            "avg_pr_completion_hours": round(sum(valid_hours) / len(valid_hours), 1) if valid_hours else None,
            "builds": len(builds),
            "builds_per_week": round(len(builds) / weeks, 1),
            "build_success_rate": round(succeeded / len(finished), 3) if finished else None,
            "deployments": len(deployments),
            "deployments_per_week": round(len(deployments) / weeks, 2),
            "production_deployments": len(prod),
            "production_deployments_per_week": round(len(prod) / weeks, 2),
            "change_failure_rate": round(prod_failed / len(prod), 3) if prod else None,
            "deployments_by_week": dict(sorted(by_week.items())),
            "builds_by_week": dict(sorted(builds_by_week.items())),
            "commits_by_week": dict(sorted(commits_by_week.items())),
        }

    @staticmethod
    def _boards(ctx: RunContext, weeks: float) -> dict:
        boards = ctx.snapshot.get("boards") or {}
        items = boards.get("work_items", [])
        if not items:
            return {"total": 0}
        by_type = Counter(w["type"] for w in items)
        by_category = Counter(w.get("state_category") or "Unknown" for w in items)
        is_closed = [w for w in items if w.get("state_category") in CLOSED_CATEGORIES or w.get("closed")]
        open_items = [w for w in items if w not in is_closed]
        lead = [days_between(w["created"], w["closed"]) for w in is_closed if w.get("closed")]
        cycle = [days_between(w["activated"], w["closed"]) for w in is_closed if w.get("closed") and w.get("activated")]
        per_type = {}
        for wtype in by_type:
            typed = [w for w in is_closed if w["type"] == wtype and w.get("closed")]
            per_type[wtype] = {
                "total": by_type[wtype],
                "open": sum(1 for w in open_items if w["type"] == wtype),
                "lead_time_days": summarise(days_between(w["created"], w["closed"]) for w in typed),
                "cycle_time_days": summarise(days_between(w["activated"], w["closed"]) for w in typed if w.get("activated")),
            }
        window_start = ctx.now - timedelta(days=ctx.settings.history_days)
        closed_window = [w for w in is_closed if (dt := parse_date(w.get("closed"))) and dt >= window_start]
        assignees = Counter(w.get("assigned_to") or "Unassigned" for w in open_items)
        aging = [w for w in open_items if (days_between(w["created"], ctx.now) or 0) > ctx.settings.stale_days]
        return {
            "total": len(items),
            "truncated": boards.get("truncated", False),
            "by_type": dict(by_type.most_common()),
            "by_state_category": dict(by_category.most_common()),
            "open": len(open_items),
            "closed": len(is_closed),
            "lead_time_days": summarise(lead),
            "cycle_time_days": summarise(cycle),
            "throughput_per_week": round(len(closed_window) / weeks, 1),
            "per_type": per_type,
            "open_by_assignee": dict(assignees.most_common(20)),
            "unassigned_open": assignees.get("Unassigned", 0),
            "aging_open": len(aging),
            "open_bugs": sum(1 for w in open_items if w["type"] == "Bug"),
        }

    @staticmethod
    def _tests(ctx: RunContext) -> dict:
        tp = ctx.snapshot.get("test_plans") or {}
        plans = tp.get("plans", [])
        cases = sum(p.get("test_case_count", 0) for p in plans)
        automated = sum(p.get("automated_count", 0) for p in plans)
        return {
            "plans": len(plans),
            "suites": sum(len(p.get("suites", [])) for p in plans),
            "test_cases": cases,
            "automated": automated,
            "automation_coverage": round(automated / cases, 3) if cases else None,
            "runs": (tp.get("runs") or {}).get("count", 0),
            "run_pass_rate": (tp.get("runs") or {}).get("pass_rate"),
        }

    @staticmethod
    def _cost(ctx: RunContext) -> dict:
        s, cfg = ctx.snapshot, ctx.settings
        builds = s.get("builds", [])
        hosted, self_hosted = [], []
        hosted_minutes = self_minutes = 0.0
        for b in builds:
            start, end = parse_date(b.get("start_time")), parse_date(b.get("finish_time"))
            if not start or not end:
                continue
            minutes = (end - start).total_seconds() / 60
            if is_hosted_build(b):
                hosted.append((start, end))
                hosted_minutes += minutes
            else:
                self_hosted.append((start, end))
                self_minutes += minutes
        scale = 30 / max(cfg.history_days, 1)
        peak_hosted, peak_self = peak_concurrency(hosted), peak_concurrency(self_hosted)
        hosted_jobs_cost = max(peak_hosted - 1, 0) * cfg.hosted_parallel_job_cost
        self_jobs_cost = max(peak_self - 1, 0) * cfg.self_hosted_parallel_job_cost
        return {
            "hosted_minutes_per_month": round(hosted_minutes * scale),
            "self_hosted_minutes_per_month": round(self_minutes * scale),
            "peak_hosted_concurrency": peak_hosted,
            "peak_self_hosted_concurrency": peak_self,
            "estimated_parallel_job_cost_per_month": round(hosted_jobs_cost + self_jobs_cost, 2),
            "estimated_hosted_minute_equivalent_cost": round(hosted_minutes * scale * cfg.hosted_minute_cost, 2),
            "assumptions": (f"First parallel job free; hosted parallel job ${cfg.hosted_parallel_job_cost}/month; "
                            f"self-hosted ${cfg.self_hosted_parallel_job_cost}/month; peak concurrency observed "
                            f"over {cfg.history_days} days. Agent VM costs are not included."),
        }
