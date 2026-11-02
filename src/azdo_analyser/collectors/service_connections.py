"""Service connections (service endpoints) with usage history and authorisations."""

from __future__ import annotations

from azdo_analyser.collectors.common import PREVIEW, pipeline_permissions, resource_checks
from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name, parse_date

TYPE_LABELS = {
    "azurerm": "Azure Resource Manager",
    "azure": "Azure Classic",
    "github": "GitHub",
    "githubenterprise": "GitHub Enterprise",
    "dockerregistry": "Docker Registry",
    "kubernetes": "Kubernetes",
    "generic": "Generic",
    "externalnugetfeed": "NuGet",
    "externalnpmregistry": "npm",
    "ssh": "SSH",
    "git": "Git (other)",
    "azureservicebus": "Azure Service Bus",
}

EXPIRY_KEYS = ("expiresOn", "expiry", "expirationDate", "secretExpiry", "spnExpiry")
SAFE_DATA_KEYS = ("subscriptionId", "subscriptionName", "environment", "scopeLevel", "creationMode",
                  "managementGroupId", "managementGroupName", "azureSpnRoleAssignmentId", "registrytype",
                  "authorizationType", "workloadIdentityFederationSubject", "identityType")


@register_collector("service_connections", order=80, depends_on=("project",))
class ServiceConnectionCollector(Collector):
    """Service connections, authentication scheme, execution history and pipeline permissions."""

    section = "service_connections"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        endpoints = ctx.safe("service-endpoints",
                             lambda: c.get_all(f"{p}/_apis/serviceendpoint/endpoints", {"includeFailed": "true"}), [])
        org_url = ctx.settings.organisation_url
        results = []
        for ep in endpoints:
            eid = ep["id"]
            history = ctx.safe(
                f"execution-history:{ep.get('name')}",
                lambda: c.get_all(f"{p}/_apis/serviceendpoint/{eid}/executionhistory", {"top": 100},
                                  api_version=PREVIEW, limit=200),
                [],
            )
            used_by, last_used = {}, None
            for h in history:
                data = h.get("data") or {}
                definition = data.get("definition") or {}
                key = (data.get("planType"), definition.get("id"))
                used_by.setdefault(key, {"definition_id": definition.get("id"), "definition_name": definition.get("name"),
                                         "plan_type": data.get("planType"), "runs": 0})["runs"] += 1
                finished = parse_date(data.get("finishTime") or data.get("startTime"))
                if finished and (last_used is None or finished > last_used):
                    last_used = finished
            data = ep.get("data") or {}
            auth = ep.get("authorization") or {}
            expiry = next((data[k] for k in EXPIRY_KEYS if data.get(k)), None)
            results.append({
                "id": eid,
                "name": ep.get("name"),
                "type": ep.get("type"),
                "type_label": TYPE_LABELS.get((ep.get("type") or "").lower(), ep.get("type")),
                "auth_scheme": auth.get("scheme"),
                "url": ep.get("url"),
                "description": ep.get("description") or "",
                "is_ready": ep.get("isReady", True),
                "is_shared": bool(ep.get("isShared")),
                "shared_projects": len(ep.get("serviceEndpointProjectReferences") or []),
                "owner": ep.get("owner"),
                "created_by": identity_name(ep.get("createdBy")),
                "data": {k: data[k] for k in SAFE_DATA_KEYS if k in data},
                "expiry": expiry,
                "operation_status": (ep.get("operationStatus") or {}).get("state"),
                "last_used": last_used.isoformat() if last_used else None,
                "execution_count": len(history),
                "used_by": list(used_by.values()),
                "web_url": f"{org_url}/{p}/_settings/adminservices?resourceId={eid}",
                "checks": resource_checks(ctx, "endpoint", eid),
                **pipeline_permissions(ctx, "endpoint", eid),
            })
        return results
