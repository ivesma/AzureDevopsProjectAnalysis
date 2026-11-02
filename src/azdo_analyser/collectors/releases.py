"""Classic release pipelines and deployments (vsrm service)."""

from __future__ import annotations

from datetime import timedelta

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name

CONDITION_TYPES = {1: "event", 2: "environmentState", 4: "artifact"}
PHASE_TYPES = {1: "agent", 2: "agentless", 4: "deploymentGroup", 6: "deploymentGroup"}


def _approvals(block: dict | None) -> dict:
    approvals = (block or {}).get("approvals") or []
    approvers = [identity_name(a.get("approver")) for a in approvals if not a.get("isAutomated")]
    return {
        "automated": all(a.get("isAutomated") for a in approvals) if approvals else True,
        "approvers": [a for a in approvers if a],
        "required_count": ((block or {}).get("approvalOptions") or {}).get("requiredApproverCount"),
        "release_creator_can_approve": ((block or {}).get("approvalOptions") or {}).get(
            "releaseCreatorCanBeApprover"),
    }


def _gates(block: dict | None) -> dict:
    block = block or {}
    options = block.get("gatesOptions") or {}
    names = []
    for gate in block.get("gates") or []:
        for task in gate.get("tasks") or []:
            if task.get("enabled", True):
                names.append(task.get("name"))
    return {"enabled": bool(options.get("isEnabled")) and bool(names), "gates": names}


def normalise_stage(env: dict, tasks: dict[str, dict]) -> dict:
    phases, steps = [], []
    for phase in env.get("deployPhases") or []:
        deploy_input = phase.get("deploymentInput") or {}
        phases.append({
            "name": phase.get("name"),
            "type": PHASE_TYPES.get(phase.get("phaseType"), str(phase.get("phaseType"))),
            "queue_id": deploy_input.get("queueId"),
            "demands": [str(d) for d in deploy_input.get("demands") or []],
        })
        for task in phase.get("workflowTasks") or []:
            meta = tasks.get(task.get("taskId"), {})
            steps.append({
                "phase": phase.get("name"),
                "name": task.get("name"),
                "task": meta.get("name") or task.get("taskId"),
                "version": task.get("version"),
                "enabled": task.get("enabled", True),
                "continue_on_error": task.get("continueOnError", False),
                "inputs": task.get("inputs") or {},
            })
    conditions = [
        {"type": CONDITION_TYPES.get(c.get("conditionType"), str(c.get("conditionType"))), "name": c.get("name"),
         "value": c.get("value")}
        for c in env.get("conditions") or []
    ]
    return {
        "id": env.get("id"),
        "name": env.get("name"),
        "rank": env.get("rank"),
        "owner": identity_name(env.get("owner")),
        "pre_approvals": _approvals(env.get("preDeployApprovals")),
        "post_approvals": _approvals(env.get("postDeployApprovals")),
        "pre_gates": _gates(env.get("preDeploymentGates")),
        "post_gates": _gates(env.get("postDeploymentGates")),
        "conditions": conditions,
        "depends_on": [c["name"] for c in conditions if c["type"] == "environmentState"],
        "phases": phases,
        "steps": steps,
        "variables": [{"name": k, "is_secret": bool(v.get("isSecret"))} for k, v in (env.get("variables") or {}).items()],
        "variable_group_ids": env.get("variableGroups") or [],
        "retention_days": (env.get("retentionPolicy") or {}).get("daysToKeep"),
    }


@register_collector("releases", order=40, depends_on=("pipelines",))
class ReleaseCollector(Collector):
    """Classic release definitions (stages, approvals, gates, tasks) and deployment history."""

    section = "release_pipelines"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        tasks = ctx.snapshot.get("tasks", {})
        refs = ctx.safe("release-definitions", lambda: c.get_all(f"{p}/_apis/release/definitions", service="vsrm"), [])
        window_start = ctx.now - timedelta(days=ctx.settings.history_days)
        deployments = ctx.safe(
            "deployments",
            lambda: c.get_all(f"{p}/_apis/release/deployments",
                              {"minStartedTime": window_start.isoformat(), "$top": 100},
                              service="vsrm", limit=ctx.settings.max_builds),
            [],
        )
        ctx.snapshot["release_deployments"] = [
            {
                "id": d.get("id"),
                "release_definition": (d.get("releaseDefinition") or {}).get("name"),
                "release_definition_id": (d.get("releaseDefinition") or {}).get("id"),
                "release": (d.get("release") or {}).get("name"),
                "stage": (d.get("releaseEnvironment") or {}).get("name"),
                "status": d.get("deploymentStatus"),
                "reason": d.get("reason"),
                "started_on": d.get("startedOn"),
                "completed_on": d.get("completedOn"),
                "requested_for": identity_name(d.get("requestedFor")),
            }
            for d in deployments
        ]
        releases = []
        for ref in refs:
            d = ctx.safe(f"release-definition:{ref.get('name')}",
                         lambda r=ref: c.get(f"{p}/_apis/release/definitions/{r['id']}", service="vsrm"), ref)
            links = d.get("_links") or {}
            releases.append({
                "id": d.get("id"),
                "name": d.get("name"),
                "folder": d.get("path", "\\"),
                "description": d.get("description") or "",
                "created_by": identity_name(d.get("createdBy")),
                "created_on": d.get("createdOn"),
                "modified_by": identity_name(d.get("modifiedBy")),
                "modified_on": d.get("modifiedOn"),
                "web_url": (links.get("web") or {}).get("href"),
                "artifacts": [
                    {
                        "alias": a.get("alias"),
                        "type": a.get("type"),
                        "is_primary": bool(a.get("isPrimary")),
                        "source": ((a.get("definitionReference") or {}).get("definition") or {}).get("name"),
                        "source_id": ((a.get("definitionReference") or {}).get("definition") or {}).get("id"),
                    }
                    for a in d.get("artifacts") or []
                ],
                "triggers": [
                    {"type": {1: "artifact", 2: "schedule", 3: "pullRequest"}.get(t.get("triggerType"), "other"),
                     "artifact_alias": t.get("artifactAlias")}
                    for t in d.get("triggers") or []
                ],
                "variables": [{"name": k, "is_secret": bool(v.get("isSecret"))}
                              for k, v in (d.get("variables") or {}).items()],
                "variable_group_ids": d.get("variableGroups") or [],
                "stages": sorted((normalise_stage(e, tasks) for e in d.get("environments") or []),
                                 key=lambda s: s.get("rank") or 0),
            })
        return releases
