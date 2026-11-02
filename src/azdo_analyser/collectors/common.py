"""Helpers shared by collectors for protected resources (checks and pipeline permissions)."""

from __future__ import annotations

from azdo_analyser.core.context import RunContext
from azdo_analyser.core.utils import identity_name

PREVIEW = "7.1-preview.1"

# definitionRef names of built-in "Task Check" checks -> UI names
CHECK_NAMES = {
    "evaluatebranchProtection": "Branch control",
    "businessHours": "Business hours",
    "AzureFunction": "Invoke Azure Function",
    "InvokeRESTAPI": "Invoke REST API",
    "queryAzureMonitorAlerts": "Query Azure Monitor alerts",
    "ExclusiveLock": "Exclusive lock",
}


def resource_checks(ctx: RunContext, resource_type: str, resource_id: str | int) -> list[dict]:
    """Approvals & checks configured on a protected resource (environment, endpoint, queue, ...)."""
    raw = ctx.safe(
        f"checks:{resource_type}:{resource_id}",
        lambda: ctx.client.get_all(
            f"{ctx.project}/_apis/pipelines/checks/configurations",
            {"resourceType": resource_type, "resourceId": str(resource_id), "$expand": "settings"},
            api_version=PREVIEW,
        ),
        [],
    )
    checks = []
    for check in raw:
        settings = check.get("settings") or {}
        kind = (check.get("type") or {}).get("name", "Unknown")
        if kind == "Task Check":
            ref = (settings.get("definitionRef") or {}).get("name")
            kind = CHECK_NAMES.get(ref) or settings.get("displayName") or ref or kind
        elif kind == "ExtendsCheck":
            kind = "Required template"
        checks.append({
            "id": check.get("id"),
            "type": kind,
            "display_name": settings.get("displayName") or kind,
            "approvers": [identity_name(a) for a in settings.get("approvers") or []],
            "min_approvers": settings.get("minRequiredApprovers"),
            "requester_can_approve": not settings.get("requesterCannotBeApprover", False),
            "timeout_minutes": check.get("timeout"),
            "disabled": bool(check.get("isDisabled")),
        })
    return checks


def pipeline_permissions(ctx: RunContext, resource_type: str, resource_id: str | int) -> dict:
    """Which pipelines are authorised to use a protected resource."""
    data = ctx.safe(
        f"pipeline-permissions:{resource_type}:{resource_id}",
        lambda: ctx.client.get(
            f"{ctx.project}/_apis/pipelines/pipelinePermissions/{resource_type}/{resource_id}",
            api_version=PREVIEW,
        ),
        None,
    )
    if data is None:
        return {"open_access": None, "authorized_pipelines": []}
    return {
        "open_access": bool((data.get("allPipelines") or {}).get("authorized")),
        "authorized_pipelines": [x.get("id") for x in data.get("pipelines") or [] if x.get("authorized")],
    }
