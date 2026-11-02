"""Pipeline graphs: stage flows (YAML + classic release), pipeline dependencies and template usage."""

from __future__ import annotations

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Graph
from azdo_analyser.core.plugin import register_analyser


def stage_flow_graph(pipeline: dict, stages: list[dict], is_prod) -> Graph:
    graph = Graph(f"stage-flow-{pipeline['id']}", f"Stage flow: {pipeline['name']}", "pipelines",
                  description=f"Stages, dependencies and deployment targets for {pipeline['name']}")
    start = graph.add_node("trigger", "Trigger", "start")
    names = [s["name"] for s in stages]
    for i, stage in enumerate(stages):
        envs = sorted({j["environment"] for j in stage.get("jobs", []) if j.get("environment")})
        jobs = stage.get("jobs", [])
        parts = [stage.get("display_name") or stage["name"]]
        if jobs:
            parts.append(f"{len(jobs)} job{'s' if len(jobs) != 1 else ''}")
        if envs:
            parts.append("env: " + ", ".join(str(e) for e in envs))
        node_type = "prod" if any(is_prod(str(e)) for e in envs) or is_prod(stage["name"]) else (
            "deploy" if envs else "stage")
        if stage.get("template"):
            node_type = "template"
        graph.add_node(f"s_{stage['name']}", "\n".join(parts), node_type)
        deps = stage.get("depends_on")
        if deps is None:
            deps = [names[i - 1]] if i > 0 else []
        deps = [d for d in deps if d in names]
        if not deps:
            graph.add_edge(start, f"s_{stage['name']}")
        for dep in deps:
            graph.add_edge(f"s_{dep}", f"s_{stage['name']}", "condition" if stage.get("condition") else None)
    return graph


def release_flow_graph(release: dict, is_prod) -> Graph:
    graph = Graph(f"release-flow-{release['id']}", f"Release flow: {release['name']}", "pipelines",
                  description="Classic release pipeline artifacts, stages, approvals and gates")
    for art in release.get("artifacts", []):
        graph.add_node(f"a_{art['alias']}", f"{art['type']}: {art.get('source') or art['alias']}", "artifact")
    stage_names = {s["name"] for s in release.get("stages", [])}
    for stage in release.get("stages", []):
        labels = [stage["name"]]
        if not stage["pre_approvals"]["automated"]:
            labels.append("approval: " + ", ".join(stage["pre_approvals"]["approvers"][:2]))
        if stage["pre_gates"]["enabled"]:
            labels.append(f"gates: {len(stage['pre_gates']['gates'])}")
        node = graph.add_node(f"r_{stage['id']}", "\n".join(labels), "prod" if is_prod(stage["name"]) else "deploy")
        deps = [d for d in stage.get("depends_on", []) if d in stage_names]
        if deps:
            for dep in deps:
                dep_stage = next(s for s in release["stages"] if s["name"] == dep)
                graph.add_edge(f"r_{dep_stage['id']}", node)
        else:
            for art in release.get("artifacts", []):
                graph.add_edge(f"a_{art['alias']}", node, "release created" if art.get("is_primary") else None,
                               "solid" if art.get("is_primary") else "dashed")
            if not release.get("artifacts"):
                graph.add_edge(graph.add_node("manual", "Manual", "start"), node)
    return graph


@register_analyser("pipeline_graphs", order=20, depends_on=("yaml",))
class PipelineGraphAnalyser(Analyser):
    """Stage flow diagrams for every pipeline, the pipeline dependency graph and template usage graph."""

    def analyse(self, ctx: RunContext) -> None:
        a = ctx.assessment
        yaml_info = ctx.analysis.get("yaml", {})
        effective = yaml_info.get("pipelines", {})
        pipelines = ctx.snapshot.get("pipelines", [])
        releases = ctx.snapshot.get("release_pipelines", [])
        is_prod = ctx.settings.is_production

        for p in pipelines:
            model = effective.get(str(p["id"]))
            if model and model.get("stages"):
                a.add_graph(stage_flow_graph(p, model["stages"], is_prod))
            elif p["type"] == "classic" and p.get("classic_steps"):
                phases = []
                for step in p["classic_steps"]:
                    if step["phase"] not in phases:
                        phases.append(step["phase"])
                stages = [{"name": ph or "Phase", "depends_on": None,
                           "jobs": [{"name": ph, "environment": None}]} for ph in phases]
                a.add_graph(stage_flow_graph(p, stages, is_prod))
        for rel in releases:
            a.add_graph(release_flow_graph(rel, is_prod))

        deps = self._dependencies(ctx, pipelines, releases, effective)
        ctx.analysis["pipeline_dependencies"] = deps
        graph = Graph("pipeline-dependencies", "Pipeline dependency graph", "dependencies",
                      description="Build completion triggers, pipeline resources, release artifacts and PR validation")
        for d in deps:
            graph.add_node(f"{d['source_type']}:{d['source']}", d["source"], d["source_type"])
            graph.add_node(f"{d['target_type']}:{d['target']}", d["target"], d["target_type"])
            graph.add_edge(f"{d['source_type']}:{d['source']}", f"{d['target_type']}:{d['target']}", d["relation"],
                           "dashed" if d["relation"] in ("source", "PR validation") else "solid")
        a.add_graph(graph)
        a.add_graph(self._template_graph(pipelines, effective, yaml_info.get("templates", {})))

    @staticmethod
    def _dependencies(ctx: RunContext, pipelines: list[dict], releases: list[dict], effective: dict) -> list[dict]:
        by_id = {p["id"]: p for p in pipelines}
        by_name = {p["name"].lower(): p for p in pipelines}
        deps: list[dict] = []

        def add(source, stype, target, ttype, relation):
            row = {"source": source, "source_type": stype, "target": target, "target_type": ttype,
                   "relation": relation}
            if row not in deps:
                deps.append(row)

        for p in pipelines:
            repo = (p.get("repository") or {}).get("name")
            if repo:
                add(repo, "repository", p["name"], "pipeline", "source")
            for trig in p.get("triggers", []):
                if trig["type"] == "Build completion" and trig.get("source_definition"):
                    add(trig["source_definition"], "pipeline", p["name"], "pipeline", "build completion")
            model = effective.get(str(p["id"])) or {}
            for res in model.get("pipeline_resources", []):
                source = res.get("source")
                if source:
                    target = by_name.get(str(source).lower())
                    add(target["name"] if target else source, "pipeline", p["name"], "pipeline",
                        "triggers" if res.get("trigger") else "consumes artifacts")
        for repo in ctx.snapshot.get("repositories", []):
            for def_id in repo.get("default_branch_policies", {}).get("build_validation_definitions", []):
                if def_id in by_id:
                    add(repo["name"], "repository", by_id[def_id]["name"], "pipeline", "PR validation")
        for rel in releases:
            for art in rel.get("artifacts", []):
                if art["type"] == "Build" and art.get("source"):
                    add(art["source"], "pipeline", rel["name"], "release",
                        "artifact (CD trigger)" if any(t.get("artifact_alias") == art["alias"]
                                                       for t in rel.get("triggers", [])) else "artifact")
                elif art.get("source"):
                    add(art["source"], "repository", rel["name"], "release", f"{art['type']} artifact")
        return deps

    @staticmethod
    def _template_graph(pipelines: list[dict], effective: dict, templates: dict) -> Graph:
        graph = Graph("template-usage", "Template usage graph", "dependencies",
                      description="Which pipelines extend/include which templates (transitively)")
        for p in pipelines:
            model = effective.get(str(p["id"]))
            if not model:
                continue
            root = model["root_key"]
            graph.add_node(root, p["name"], "pipeline")
            for source, target, level in model.get("template_edges", []):
                if source != root:
                    graph.add_node(source, source.split(":", 1)[-1].lstrip("/"), "template")
                graph.add_node(target, f"{target.split(':', 1)[0]}: {target.split(':', 1)[-1].lstrip('/')}", "template")
                graph.add_edge(source, target, level, "thick" if level == "extends" else "solid")
            for ref in model.get("unresolved_templates", []):
                node = graph.add_node(f"unresolved:{ref}", f"{ref} (unresolved)", "missing")
                graph.add_edge(root, node, "unresolved", "dashed")
        return graph
