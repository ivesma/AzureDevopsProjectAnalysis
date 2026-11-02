"""Cross-resource dependency engine.

Builds usage matrices and dependency graphs for repositories, variable groups, secrets & Key Vault,
service connections, agent pools, environments and feeds.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Graph
from azdo_analyser.core.plugin import register_analyser

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
HOSTED_IMAGE_POOL = "Azure Pipelines"


class Usage:
    """Collects (resource, consumer) rows without duplicates."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._seen: set[tuple] = set()

    def add(self, resource: str, consumer: str, consumer_type: str, source: str, **extra) -> None:
        key = (resource, consumer, consumer_type, source)
        if key in self._seen:
            return
        self._seen.add(key)
        self.rows.append({"resource": resource, "consumer": consumer, "consumer_type": consumer_type,
                          "source": source, **extra})

    def consumers_of(self, resource: str) -> set[str]:
        return {r["consumer"] for r in self.rows if r["resource"] == resource}


@register_analyser("dependencies", order=30, depends_on=("yaml",))
class DependencyEngine(Analyser):
    """Repository, variable, service connection, agent pool, environment and feed dependency mapping."""

    def analyse(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.effective = ctx.analysis.get("yaml", {}).get("pipelines", {})
        self.pipelines = ctx.snapshot.get("pipelines", [])
        self.releases = ctx.snapshot.get("release_pipelines", [])
        self._service_connections()
        self._variables()
        self._agent_pools()
        self._environments()
        self._repositories()
        self._feeds()

    # ------------------------------------------------------------------ service connections
    def _service_connections(self) -> None:
        ctx = self.ctx
        endpoints = ctx.snapshot.get("service_connections", [])
        by_id = {e["id"].lower(): e for e in endpoints}
        by_name = {e["name"].lower(): e for e in endpoints}
        usage = Usage()

        def resolve(value: str):
            value = str(value).strip()
            return by_id.get(value.lower()) or by_name.get(value.lower())

        for p in self.pipelines:
            model = self.effective.get(str(p["id"])) or {}
            for name in model.get("service_connections", []):
                ep = resolve(name)
                usage.add(ep["name"] if ep else name, p["name"], "pipeline", "yaml", known=bool(ep))
            for step in p.get("classic_steps", []):
                for value in step["inputs"].values():
                    if isinstance(value, str) and GUID_RE.match(value.strip()) and (ep := resolve(value)):
                        usage.add(ep["name"], p["name"], "pipeline", "classic task", known=True)
        for rel in self.releases:
            for stage in rel.get("stages", []):
                for step in stage.get("steps", []):
                    for value in step["inputs"].values():
                        if isinstance(value, str) and (ep := resolve(value)):
                            usage.add(ep["name"], f"{rel['name']} / {stage['name']}", "release stage", "release task",
                                      known=True, release=rel["name"], stage=stage["name"])
        for ep in endpoints:
            for used in ep.get("used_by", []):
                kind = "release" if (used.get("plan_type") or "").lower() == "release" else "pipeline"
                usage.add(ep["name"], used.get("definition_name") or str(used.get("definition_id")), kind,
                          "execution history", known=True)
        for env in ctx.snapshot.get("environments", []):
            for res in env.get("resources", []):
                if res.get("service_endpoint_id") and (ep := resolve(res["service_endpoint_id"])):
                    usage.add(ep["name"], env["name"], "environment", "kubernetes resource", known=True)
        for vg in ctx.snapshot.get("variable_groups", []):
            if vg.get("service_endpoint_id") and (ep := resolve(vg["service_endpoint_id"])):
                usage.add(ep["name"], vg["name"], "variable group", "key vault link", known=True)

        ctx.analysis["service_connection_usage"] = usage.rows
        graph = Graph("service-connections", "Service connection map", "dependencies",
                      description="Pipelines, release stages, environments and variable groups using each connection")
        for ep in endpoints:
            graph.add_node(f"sc:{ep['name']}", f"{ep['name']}\n({ep.get('type_label') or ep.get('type')})",
                           "service_connection")
        for row in usage.rows:
            sc_node = graph.add_node(f"sc:{row['resource']}", row["resource"],
                                     "service_connection" if row.get("known") else "missing")
            consumer = graph.add_node(f"{row['consumer_type']}:{row['consumer']}", row["consumer"],
                                      row["consumer_type"].replace(" ", "_"))
            graph.add_edge(consumer, sc_node, None, "dashed" if row["source"] == "execution history" else "solid")
        ctx.assessment.add_graph(graph)

    # ------------------------------------------------------------------------- variables
    def _variables(self) -> None:
        ctx = self.ctx
        groups = ctx.snapshot.get("variable_groups", [])
        by_id = {g["id"]: g for g in groups}
        by_name = {g["name"].lower(): g for g in groups}
        matrix = Usage()

        def group_row(group_name: str, consumer: str, ctype: str, source: str) -> None:
            g = by_name.get(group_name.lower())
            matrix.add(g["name"] if g else group_name, consumer, ctype, source,
                       group_type=(g or {}).get("type", "unknown"), secret_count=(g or {}).get("secret_count"),
                       key_vault=(g or {}).get("key_vault"), known=bool(g))

        for p in self.pipelines:
            for gid in p.get("variable_group_ids", []):
                if gid in by_id:
                    group_row(by_id[gid]["name"], p["name"], "pipeline", "definition")
            for name in (self.effective.get(str(p["id"])) or {}).get("variable_groups", []):
                group_row(name, p["name"], "pipeline", "yaml")
        for rel in self.releases:
            for gid in rel.get("variable_group_ids", []):
                if gid in by_id:
                    group_row(by_id[gid]["name"], rel["name"], "release", "release definition")
            for stage in rel.get("stages", []):
                for gid in stage.get("variable_group_ids", []):
                    if gid in by_id:
                        group_row(by_id[gid]["name"], f"{rel['name']} / {stage['name']}", "release stage", "stage scope")
        ctx.analysis["variable_group_usage"] = matrix.rows

        secrets = []
        for p in self.pipelines:
            for v in p.get("variables", []):
                if v["is_secret"]:
                    secrets.append({"owner": p["name"], "owner_type": "pipeline", "name": v["name"],
                                    "store": "pipeline variable"})
            model = self.effective.get(str(p["id"])) or {}
            for v in model.get("variables_inline", []):
                if v.get("looks_secret"):
                    secrets.append({"owner": p["name"], "owner_type": "pipeline", "name": v["name"],
                                    "store": "YAML literal (plain text)"})
            for kv in model.get("key_vaults", []):
                secrets.append({"owner": p["name"], "owner_type": "pipeline", "name": f"{kv} (all secrets)",
                                "store": "AzureKeyVault task"})
        for rel in self.releases:
            for v in rel.get("variables", []) + [v for s in rel.get("stages", []) for v in s.get("variables", [])]:
                if v["is_secret"]:
                    secrets.append({"owner": rel["name"], "owner_type": "release", "name": v["name"],
                                    "store": "release variable"})
        for g in groups:
            for v in g["variables"]:
                if v["is_secret"]:
                    secrets.append({"owner": g["name"], "owner_type": "variable group", "name": v["name"],
                                    "store": f"Key Vault: {g['key_vault']}" if g.get("key_vault") else "variable group"})
        ctx.analysis["secrets"] = secrets

        graph = Graph("variable-groups", "Variable group & Key Vault dependencies", "dependencies",
                      description="Which pipelines and releases consume which variable groups / Key Vaults")
        for g in groups:
            node = graph.add_node(f"vg:{g['name']}", f"{g['name']}\n{g['variable_count']} vars, {g['secret_count']} secret",
                                  "keyvault_group" if g.get("key_vault") else "variable_group")
            if g.get("key_vault"):
                graph.add_edge(node, graph.add_node(f"kv:{g['key_vault']}", f"Key Vault: {g['key_vault']}", "keyvault"))
        for row in matrix.rows:
            graph.add_edge(graph.add_node(f"{row['consumer_type']}:{row['consumer']}", row["consumer"],
                                          row["consumer_type"].replace(" ", "_")),
                           graph.add_node(f"vg:{row['resource']}", row["resource"],
                                          "variable_group" if row.get("known") else "missing"))
        for p in self.pipelines:
            for kv in (self.effective.get(str(p["id"])) or {}).get("key_vaults", []):
                graph.add_edge(graph.add_node(f"pipeline:{p['name']}", p["name"], "pipeline"),
                               graph.add_node(f"kv:{kv}", f"Key Vault: {kv}", "keyvault"), "AzureKeyVault task")
        ctx.assessment.add_graph(graph)

    # ----------------------------------------------------------------------- agent pools
    def _agent_pools(self) -> None:
        ctx = self.ctx
        pools = ctx.snapshot.get("agent_pools", [])
        queue_to_pool = {qid: p["name"] for p in pools for qid in p.get("queue_ids", [])}
        usage = Usage()
        for p in self.pipelines:
            model = self.effective.get(str(p["id"])) or {}
            yaml_pools = model.get("pools", [])
            for entry in yaml_pools:
                name = entry.get("name") or (HOSTED_IMAGE_POOL if entry.get("vm_image") else None)
                if name:
                    usage.add(str(name), p["name"], "pipeline", "yaml", vm_image=entry.get("vm_image"),
                              demands=", ".join(entry.get("demands") or []))
            if not yaml_pools and p.get("pool"):
                usage.add(p["pool"], p["name"], "pipeline", "definition default queue",
                          demands=", ".join(p.get("demands") or []))
        for rel in self.releases:
            for stage in rel.get("stages", []):
                for phase in stage.get("phases", []):
                    if phase.get("queue_id") in queue_to_pool:
                        usage.add(queue_to_pool[phase["queue_id"]], f"{rel['name']} / {stage['name']}", "release stage",
                                  "deploy phase", demands=", ".join(phase.get("demands") or []))
        build_counts = Counter(b.get("pool") for b in ctx.snapshot.get("builds", []) if b.get("pool"))
        ctx.analysis["agent_pool_usage"] = usage.rows
        ctx.analysis["agent_pool_builds"] = dict(build_counts)
        graph = Graph("agent-pools", "Agent pool usage", "dependencies", description="Pipelines and stages per pool")
        for pool in pools:
            graph.add_node(f"pool:{pool['name']}", f"{pool['name']}\n{'Microsoft-hosted' if pool['is_hosted'] else 'self-hosted'}"
                           f" · {len(pool.get('agents', []))} agents", "pool")
        for row in usage.rows:
            graph.add_edge(graph.add_node(f"{row['consumer_type']}:{row['consumer']}", row["consumer"],
                                          row["consumer_type"].replace(" ", "_")),
                           graph.add_node(f"pool:{row['resource']}", row["resource"], "pool"),
                           row.get("vm_image") or None)
        ctx.assessment.add_graph(graph)

    # ---------------------------------------------------------------------- environments
    def _environments(self) -> None:
        ctx = self.ctx
        envs = ctx.snapshot.get("environments", [])
        usage = Usage()
        for p in self.pipelines:
            for env in (self.effective.get(str(p["id"])) or {}).get("environments", []):
                usage.add(str(env), p["name"], "pipeline", "yaml deployment job")
        for env in envs:
            for dep in env.get("deployments", []):
                if dep.get("pipeline"):
                    usage.add(env["name"], dep["pipeline"], "pipeline", "deployment history")
        ctx.analysis["environment_usage"] = usage.rows
        graph = Graph("environments", "Environment relationships", "environments", direction="LR",
                      description="Pipelines deploying to environments, their resources and protecting checks")
        env_nodes = {e["name"].lower(): f"env:{e['name']}" for e in envs}
        for env in envs:
            is_prod = ctx.settings.is_production(env["name"])
            checks = [c["type"] for c in env.get("checks", []) if not c.get("disabled")]
            label = f"{env['name']}\nchecks: {', '.join(checks) if checks else 'none'}"
            node = graph.add_node(f"env:{env['name']}", label, "prod" if is_prod else "environment")
            for res in env.get("resources", []):
                rnode = graph.add_node(f"res:{env['name']}:{res['name']}",
                                       f"{res['type']}: {res['name']}" + (f"\nns: {res['namespace']}" if res.get("namespace")
                                                                           else ""), "resource")
                graph.add_edge(node, rnode)
        for row in usage.rows:
            env_node = env_nodes.get(row["resource"].lower())
            if not env_node:
                env_node = env_nodes[row["resource"].lower()] = graph.add_node(
                    f"env:{row['resource']}", f"{row['resource']}\n(not found)", "missing")
            graph.add_edge(graph.add_node(f"pipeline:{row['consumer']}", row["consumer"], "pipeline"), env_node,
                           "deploys", "dashed" if row["source"] == "deployment history" else "solid")
        ctx.assessment.add_graph(graph)

    # ---------------------------------------------------------------------- repositories
    def _repositories(self) -> None:
        ctx = self.ctx
        repos = ctx.snapshot.get("repositories", [])
        names = {r["name"].lower(): r["name"] for r in repos}
        deps: list[dict] = []

        def add(source: str, target: str, relation: str, via: str) -> None:
            row = {"source": source, "target": target, "relation": relation, "via": via}
            if source.lower() != target.lower() and row not in deps:
                deps.append(row)

        for p in self.pipelines:
            src = (p.get("repository") or {}).get("name")
            model = self.effective.get(str(p["id"])) or {}
            if not src:
                continue
            for key in model.get("templates", []):
                add(src, key.split(":", 1)[0], "templates", p["name"])
            for res in model.get("repositories", []):
                target = str(res.get("name") or "").split("/")[-1]
                if target and res.get("alias") in model.get("checkouts", []):
                    add(src, names.get(target.lower(), target), "checkout", p["name"])
                elif target:
                    add(src, names.get(target.lower(), target), "resource", p["name"])
        for repo in repos:
            for sub in repo.get("submodules", []):
                url = sub.get("url", "")
                target = url.rstrip("/").split("/")[-1].removesuffix(".git")
                add(repo["name"], names.get(target.lower(), target), "submodule", sub.get("path", ""))
        for rel in self.releases:
            for art in rel.get("artifacts", []):
                if art["type"] in ("Git", "GitHub", "TfsGit") and art.get("source"):
                    add(art["source"], f"release:{rel['name']}", "release artifact", rel["name"])
        ctx.analysis["repository_dependencies"] = deps
        graph = Graph("repository-dependencies", "Repository dependency map", "dependencies",
                      description="Template, checkout, resource and submodule dependencies between repositories")
        for r in repos:
            graph.add_node(f"repo:{r['name']}", r["name"], "repository_disabled" if r.get("is_disabled") else "repository")
        for d in deps:
            target_node = f"repo:{d['target']}"
            graph.add_node(target_node, d["target"], "repository" if d["target"].lower() in names else "external")
            graph.add_edge(f"repo:{d['source']}", target_node, d["relation"])
        ctx.assessment.add_graph(graph)

    # ----------------------------------------------------------------------------- feeds
    def _feeds(self) -> None:
        ctx = self.ctx
        feeds = ctx.snapshot.get("feeds", [])
        lookup = {}
        for f in feeds:
            lookup[f["name"].lower()] = f["name"]
            lookup[f["id"].lower()] = f["name"]
        consumers = defaultdict(set)
        for p in self.pipelines:
            for ref in (self.effective.get(str(p["id"])) or {}).get("feeds", []):
                name = str(ref).split("/")[-1].lower()
                if name in lookup:
                    consumers[lookup[name]].add(p["name"])
            for step in p.get("classic_steps", []):
                for value in step["inputs"].values():
                    if isinstance(value, str) and value.split("/")[-1].lower() in lookup:
                        consumers[lookup[value.split("/")[-1].lower()]].add(p["name"])
        ctx.analysis["feed_consumers"] = {k: sorted(v) for k, v in consumers.items()}
