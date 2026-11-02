"""Build/YAML pipeline definitions, runs and YAML sources."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from azdo_analyser.collectors.repositories import fetch_file
from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import days_between, identity_name, parse_date, short_ref

TRIGGER_NAMES = {
    "continuousIntegration": "CI",
    "pullRequest": "PR",
    "schedule": "Schedule",
    "buildCompletion": "Build completion",
    "gatedCheckIn": "Gated check-in",
    "batchedContinuousIntegration": "Batched CI",
}


def normalise_triggers(raw: list[dict]) -> tuple[list[dict], list[dict]]:
    triggers, schedules = [], []
    for t in raw or []:
        kind = t.get("triggerType", "unknown")
        entry = {
            "type": TRIGGER_NAMES.get(kind, kind),
            "branches": t.get("branchFilters", []),
            "paths": t.get("pathFilters", []),
            "from_yaml": t.get("settingsSourceType") == 2,
        }
        if kind == "buildCompletion":
            definition = t.get("definition") or {}
            entry["source_definition_id"] = definition.get("id")
            entry["source_definition"] = definition.get("name")
        if kind == "schedule":
            for sched in t.get("schedules", []):
                schedules.append({
                    "branches": sched.get("branchFilters", []),
                    "days": sched.get("daysToBuild"),
                    "time": f"{sched.get('startHours', 0):02d}:{sched.get('startMinutes', 0):02d}",
                    "timezone": sched.get("timeZoneId"),
                    "only_on_changes": sched.get("scheduleOnlyWithChanges"),
                })
        triggers.append(entry)
    return triggers, schedules


def classic_steps(process: dict, tasks: dict[str, dict]) -> list[dict]:
    steps = []
    for phase in process.get("phases", []) or []:
        for step in phase.get("steps", []) or []:
            task = step.get("task") or {}
            meta = tasks.get(task.get("id"), {})
            steps.append({
                "phase": phase.get("name"),
                "display_name": step.get("displayName"),
                "task_id": task.get("id"),
                "task": meta.get("name") or task.get("id"),
                "version": task.get("versionSpec"),
                "enabled": step.get("enabled", True),
                "continue_on_error": step.get("continueOnError", False),
                "inputs": step.get("inputs") or {},
            })
    return steps


def run_stats(builds: list[dict]) -> dict[str, Any]:
    results = Counter(b.get("result") or b.get("status") for b in builds)
    finished = [b for b in builds if b.get("result")]
    durations = [d * 1440 for b in finished if (d := days_between(b.get("startTime"), b.get("finishTime"))) is not None]
    succeeded = results.get("succeeded", 0)
    rate = round(succeeded / len(finished), 3) if finished else None
    last = max(builds, key=lambda b: parse_date(b.get("queueTime")) or parse_date("1970-01-01"), default=None)
    return {
        "total": len(builds),
        "succeeded": succeeded,
        "failed": results.get("failed", 0),
        "partially_succeeded": results.get("partiallySucceeded", 0),
        "canceled": results.get("canceled", 0),
        "success_rate": rate,
        "avg_duration_min": round(sum(durations) / len(durations), 1) if durations else None,
        "total_duration_min": round(sum(durations), 1),
        "last_run": last.get("queueTime") if last else None,
        "last_result": (last.get("result") or last.get("status")) if last else None,
    }


@register_collector("pipelines", order=30, depends_on=("repositories",))
class PipelineCollector(Collector):
    """Build and YAML pipeline definitions, run history and YAML sources."""

    section = "pipelines"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p, s = ctx.client, ctx.project, ctx.settings
        task_catalogue = ctx.safe("task-catalogue", lambda: c.get_all("_apis/distributedtask/tasks"), [])
        tasks = {}
        for t in task_catalogue:
            tasks.setdefault(t.get("id"), {"name": t.get("name"), "friendly_name": t.get("friendlyName"),
                                           "category": t.get("category"), "deprecated": bool(t.get("deprecated"))})
        ctx.snapshot["tasks"] = tasks

        refs = ctx.safe("build-definitions",
                        lambda: c.get_all(f"{p}/_apis/build/definitions", {"queryOrder": "definitionNameAscending"}), [])
        window_start = ctx.now - timedelta(days=s.history_days)
        builds_raw = ctx.safe(
            "builds",
            lambda: c.get_all(f"{p}/_apis/build/builds",
                              {"minTime": window_start.isoformat(), "queryOrder": "finishTimeDescending"},
                              limit=s.max_builds),
            [],
        )
        builds = [self._build(b) for b in builds_raw]
        ctx.snapshot["builds"] = builds
        by_def: dict[int, list[dict]] = defaultdict(list)
        for b in builds_raw:
            by_def[(b.get("definition") or {}).get("id")].append(b)

        repos = {r["id"]: r for r in ctx.snapshot.get("repositories", [])}
        yaml_files = ctx.snapshot.setdefault("yaml_files", {})
        pipelines = []
        for ref in refs:
            definition = ctx.safe(f"definition:{ref.get('name')}",
                                  lambda r=ref: c.get(f"{p}/_apis/build/definitions/{r['id']}"), ref)
            pipeline = self._definition(ctx, definition, tasks)
            pipeline["runs"] = run_stats(by_def.get(definition.get("id"), []))
            self._attach_yaml(ctx, pipeline, repos, yaml_files)
            pipelines.append(pipeline)
        return pipelines

    @staticmethod
    def _build(b: dict) -> dict:
        return {
            "id": b.get("id"),
            "number": b.get("buildNumber"),
            "definition_id": (b.get("definition") or {}).get("id"),
            "definition_name": (b.get("definition") or {}).get("name"),
            "status": b.get("status"),
            "result": b.get("result"),
            "reason": b.get("reason"),
            "queue_time": b.get("queueTime"),
            "start_time": b.get("startTime"),
            "finish_time": b.get("finishTime"),
            "source_branch": short_ref(b.get("sourceBranch")),
            "requested_for": identity_name(b.get("requestedFor")),
            "pool": ((b.get("queue") or {}).get("pool") or {}).get("name"),
            "pool_hosted": ((b.get("queue") or {}).get("pool") or {}).get("isHosted"),
        }

    def _definition(self, ctx: RunContext, d: dict, tasks: dict) -> dict:
        process = d.get("process") or {}
        repo = d.get("repository") or {}
        queue = d.get("queue") or {}
        triggers, schedules = normalise_triggers(d.get("triggers", []))
        variables = [
            {"name": k, "is_secret": bool(v.get("isSecret")), "allow_override": bool(v.get("allowOverride")),
             "value": None if v.get("isSecret") else v.get("value")}
            for k, v in (d.get("variables") or {}).items()
        ]
        folder = (d.get("path") or "\\").replace("/", "\\")
        is_yaml = process.get("type") == 2
        links = d.get("_links") or {}
        return {
            "id": d.get("id"),
            "name": d.get("name"),
            "folder": folder,
            "full_name": f"{folder.rstrip(chr(92))}\\{d.get('name')}",
            "type": "yaml" if is_yaml else "classic",
            "yaml_path": process.get("yamlFilename") if is_yaml else None,
            "repository": {"id": repo.get("id"), "name": repo.get("name"), "type": repo.get("type"),
                           "default_branch": short_ref(repo.get("defaultBranch"))},
            "queue_status": d.get("queueStatus", "enabled"),
            "triggers": triggers,
            "schedules": schedules,
            "variables": variables,
            "variable_groups": [g.get("name") for g in d.get("variableGroups") or []],
            "variable_group_ids": [g.get("id") for g in d.get("variableGroups") or []],
            "queue": queue.get("name"),
            "pool": (queue.get("pool") or {}).get("name"),
            "pool_hosted": (queue.get("pool") or {}).get("isHosted"),
            "demands": [str(x) for x in d.get("demands") or []],
            "classic_steps": [] if is_yaml else classic_steps(process, tasks),
            "author": identity_name(d.get("authoredBy")),
            "created_date": d.get("createdDate"),
            "revision": d.get("revision"),
            "web_url": (links.get("web") or {}).get("href"),
            "retention_rules": len(d.get("retentionRules") or []),
            "yaml_key": None,
            "yaml_status": None,
        }

    @staticmethod
    def _attach_yaml(ctx: RunContext, pipeline: dict, repos: dict, yaml_files: dict) -> None:
        if pipeline["type"] != "yaml":
            return
        repo = pipeline["repository"]
        path = "/" + (pipeline["yaml_path"] or "azure-pipelines.yml").lstrip("/")
        if repo.get("type") != "TfsGit":
            pipeline["yaml_status"] = f"external repository ({repo.get('type')}) - YAML not retrieved"
        else:
            key = f"{repo.get('name')}:{path}"
            if key not in yaml_files:
                branch = repo.get("default_branch") or (repos.get(repo.get("id")) or {}).get("default_branch")
                content = fetch_file(ctx, repo["id"], path, branch)
                if content is not None:
                    yaml_files[key] = content
            pipeline["yaml_key"] = key if key in yaml_files else None
            pipeline["yaml_status"] = "retrieved" if key in yaml_files else "not found"
        if ctx.settings.expand_yaml_via_preview:
            preview = ctx.safe(
                f"preview:{pipeline['name']}",
                lambda: ctx.client.post(f"{ctx.project}/_apis/pipelines/{pipeline['id']}/preview",
                                        {"previewRun": True}, api_version="7.1-preview.1"),
                {},
            )
            if preview.get("finalYaml"):
                ctx.snapshot.setdefault("expanded_yaml", {})[str(pipeline["id"])] = preview["finalYaml"]
