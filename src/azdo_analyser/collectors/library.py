"""Library assets: variable groups and secure files."""

from __future__ import annotations

from azdo_analyser.collectors.common import PREVIEW, pipeline_permissions, resource_checks
from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name


@register_collector("library", order=70, depends_on=("project",))
class LibraryCollector(Collector):
    """Variable groups (incl. Key Vault linked) and secure files with pipeline authorisations."""

    def collect(self, ctx: RunContext) -> None:
        c, p = ctx.client, ctx.project
        groups = ctx.safe("variable-groups", lambda: c.get_all(f"{p}/_apis/distributedtask/variablegroups"), [])
        ctx.snapshot["variable_groups"] = [self._group(ctx, g) for g in groups]
        files = ctx.safe("secure-files",
                         lambda: c.get_all(f"{p}/_apis/distributedtask/securefiles", api_version=PREVIEW), [])
        ctx.snapshot["secure_files"] = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "created_by": identity_name(f.get("createdBy")),
                "created_on": f.get("createdOn"),
                "modified_by": identity_name(f.get("modifiedBy")),
                "modified_on": f.get("modifiedOn"),
                "checks": resource_checks(ctx, "securefile", f.get("id")),
                **pipeline_permissions(ctx, "securefile", f.get("id")),
            }
            for f in files
        ]

    @staticmethod
    def _group(ctx: RunContext, g: dict) -> dict:
        provider = g.get("providerData") or {}
        is_kv = g.get("type") == "AzureKeyVault"
        variables = [
            {"name": k, "is_secret": bool(v.get("isSecret")) or is_kv, "has_value": v.get("value") not in (None, "")}
            for k, v in (g.get("variables") or {}).items()
        ]
        return {
            "id": g.get("id"),
            "name": g.get("name"),
            "type": g.get("type", "Vsts"),
            "description": g.get("description") or "",
            "variables": variables,
            "variable_count": len(variables),
            "secret_count": sum(1 for v in variables if v["is_secret"]),
            "key_vault": provider.get("vault") if is_kv else None,
            "service_endpoint_id": provider.get("serviceEndpointId"),
            "created_by": identity_name(g.get("createdBy")),
            "created_on": g.get("createdOn"),
            "modified_by": identity_name(g.get("modifiedBy")),
            "modified_on": g.get("modifiedOn"),
            "shared_projects": len(g.get("variableGroupProjectReferences") or []),
            "checks": resource_checks(ctx, "variablegroup", g.get("id")),
            **pipeline_permissions(ctx, "variablegroup", g.get("id")),
        }
