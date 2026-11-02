"""Agent pools, project queues and agents."""

from __future__ import annotations

from azdo_analyser.collectors.common import pipeline_permissions, resource_checks
from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import identity_name

INTERESTING_SYSTEM_CAPS = ("Agent.OS", "Agent.OSArchitecture", "Agent.Version", "Agent.ComputerName")


@register_collector("agent_pools", order=60, depends_on=("project",))
class AgentPoolCollector(Collector):
    """Microsoft-hosted and self-hosted pools, agents, capabilities and project queue mapping."""

    section = "agent_pools"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        queues = ctx.safe("queues", lambda: c.get_all(f"{p}/_apis/distributedtask/queues"), [])
        pools = ctx.safe("pools", lambda: c.get_all("_apis/distributedtask/pools"), [])
        queues_by_pool: dict[int, list[dict]] = {}
        for q in queues:
            queues_by_pool.setdefault((q.get("pool") or {}).get("id"), []).append(q)

        results = []
        for pool in pools:
            pool_queues = queues_by_pool.get(pool["id"], [])
            if not pool_queues:
                continue  # pool not linked to this project
            hosted = bool(pool.get("isHosted"))
            agents = []
            if not hosted:
                raw = ctx.safe(f"agents:{pool.get('name')}",
                               lambda pl=pool: c.get_all(f"_apis/distributedtask/pools/{pl['id']}/agents",
                                                         {"includeCapabilities": "true"}), [])
                for a in raw:
                    system = a.get("systemCapabilities") or {}
                    agents.append({
                        "id": a.get("id"),
                        "name": a.get("name"),
                        "version": a.get("version"),
                        "status": a.get("status"),
                        "enabled": a.get("enabled", True),
                        "os": a.get("osDescription") or system.get("Agent.OS"),
                        "created_on": a.get("createdOn"),
                        "system_capabilities": {k: system[k] for k in INTERESTING_SYSTEM_CAPS if k in system},
                        "system_capability_count": len(system),
                        "user_capabilities": a.get("userCapabilities") or {},
                    })
            queue = pool_queues[0]
            results.append({
                "id": pool["id"],
                "name": pool.get("name"),
                "is_hosted": hosted,
                "pool_type": pool.get("poolType", "automation"),
                "is_elastic": pool.get("isElastic", bool(pool.get("agentCloudId"))),
                "size": pool.get("size", len(agents)),
                "auto_provision": pool.get("autoProvision"),
                "auto_update": pool.get("autoUpdate"),
                "owner": identity_name(pool.get("owner")),
                "queue_ids": [q["id"] for q in pool_queues],
                "queue_names": [q.get("name") for q in pool_queues],
                "agents": agents,
                "online_agents": sum(1 for a in agents if a["status"] == "online" and a["enabled"]),
                "checks": resource_checks(ctx, "queue", queue["id"]),
                **pipeline_permissions(ctx, "queue", queue["id"]),
            })
        return results
