"""Pipeline environments: resources, checks and deployment history."""

from __future__ import annotations

from azdo_analyser.collectors.common import pipeline_permissions, resource_checks
from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name


@register_collector("environments", order=50, depends_on=("pipelines",))
class EnvironmentCollector(Collector):
    """Environments with Kubernetes/VM resources, approvals & checks and deployment records."""

    section = "environments"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        base = f"{p}/_apis/distributedtask/environments"
        envs = ctx.safe("environments", lambda: c.get_all(base, {"$top": 500}), [])
        results = []
        for env in envs:
            eid = env["id"]
            detail = ctx.safe(f"environment:{env.get('name')}",
                              lambda: c.get(f"{base}/{eid}", {"expands": "resourceReferences"}), env)
            resources = []
            for res in detail.get("resources") or []:
                entry = {"id": res.get("id"), "name": res.get("name"), "type": res.get("type"),
                         "tags": res.get("tags") or []}
                if res.get("type") == "kubernetes":
                    k8s = ctx.safe(f"k8s:{res.get('name')}",
                                   lambda r=res: c.get(f"{base}/{eid}/providers/kubernetes/{r['id']}"), {})
                    entry.update({"namespace": k8s.get("namespace"), "cluster": k8s.get("clusterName"),
                                  "service_endpoint_id": k8s.get("serviceEndpointId")})
                resources.append(entry)
            vms = ctx.safe(f"vms:{env.get('name')}",
                           lambda: c.get_all(f"{base}/{eid}/providers/virtualmachines"), [])
            for vm in vms:
                agent = vm.get("agent") or {}
                resources.append({"id": vm.get("id"), "name": vm.get("name"), "type": "virtualMachine",
                                  "tags": vm.get("tags") or [], "agent_status": agent.get("status"),
                                  "agent_version": agent.get("version")})
            records = ctx.safe(f"deployments:{env.get('name')}",
                               lambda: c.get_all(f"{base}/{eid}/environmentdeploymentrecords", {"top": 100}, limit=500),
                               [])
            results.append({
                "id": eid,
                "name": env.get("name"),
                "description": env.get("description") or "",
                "created_by": identity_name(env.get("createdBy")),
                "created_on": env.get("createdOn"),
                "last_modified_on": env.get("lastModifiedOn"),
                "resources": resources,
                "checks": resource_checks(ctx, "environment", eid),
                **pipeline_permissions(ctx, "environment", eid),
                "deployments": [
                    {
                        "id": r.get("id"),
                        "pipeline": (r.get("definition") or {}).get("name"),
                        "pipeline_id": (r.get("definition") or {}).get("id"),
                        "run": (r.get("owner") or {}).get("name"),
                        "stage": r.get("stageName"),
                        "job": r.get("jobName"),
                        "result": r.get("result"),
                        "start_time": r.get("startTime"),
                        "finish_time": r.get("finishTime"),
                    }
                    for r in records
                ],
            })
        return results
