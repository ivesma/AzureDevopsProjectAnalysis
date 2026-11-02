"""Parses every YAML file and resolves template chains into an effective model per pipeline."""

from __future__ import annotations

from typing import Any

from azdo_analyser.analysers.yaml_parser import (FileModel, TemplateRef, extract_stages, parse_file,
                                                 resolve_template)
from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_analyser

MAX_TEMPLATE_DEPTH = 25  # Azure Pipelines allows 100 files / 20 levels of nesting


def _scalar_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if isinstance(v, (str, int, float, bool))}


class TemplateResolver:
    def __init__(self, yaml_files: dict[str, str]):
        self.yaml_files = yaml_files
        self._cache: dict[tuple, FileModel] = {}
        self.files: dict[str, FileModel] = {}
        # case-insensitive lookup (Azure Repos paths are case-insensitive)
        self._lower = {k.lower(): k for k in yaml_files}

    def lookup(self, key: str) -> str | None:
        return key if key in self.yaml_files else self._lower.get(key.lower())

    def parse(self, key: str, params: dict[str, Any] | None = None) -> FileModel:
        scalars = _scalar_params(params or {})
        cache_key = (key, tuple(sorted((k, str(v)) for k, v in scalars.items())))
        if cache_key not in self._cache:
            self._cache[cache_key] = parse_file(key, self.yaml_files[key], scalars)
        model = self._cache[cache_key]
        self.files.setdefault(key, model)
        return model


def build_effective(pipeline: dict, root: FileModel, resolver: TemplateResolver,
                    templates_index: dict[str, dict]) -> dict:
    aliases = {r["alias"]: r for r in root.repositories if r.get("alias")}
    effective: dict[str, Any] = {
        "pipeline_id": pipeline["id"],
        "name": pipeline["name"],
        "root_key": root.key,
        "parse_error": root.error,
        "trigger": root.trigger,
        "pr": root.pr,
        "schedules": root.schedules,
        "parameters": root.parameters,
        "variables_inline": list(root.variables_inline),
        "repositories": root.repositories,
        "pipeline_resources": root.pipeline_resources,
        "containers": root.containers,
        "extends": root.extends,
        "templates": [],
        "template_edges": [],
        "unresolved_templates": [],
        "unpinned_templates": [],
        "tasks": [], "service_connections": [], "dynamic_service_connections": [], "environments": [],
        "pools": [], "checkouts": [], "key_vaults": [], "feeds": [], "variable_groups": [], "scripts": [],
        "continue_on_error": 0, "persist_credentials": False, "max_template_depth": 0,
    }
    models = [root]

    def walk(model: FileModel, depth: int, visited: tuple[str, ...]) -> None:
        effective["max_template_depth"] = max(effective["max_template_depth"], depth)
        for ref in model.templates:
            repo, path, pinned = resolve_template(ref, model, aliases)
            key = resolver.lookup(f"{repo}:{path}") if repo else None
            ref.repo, ref.path, ref.pinned = repo, path, pinned
            if pinned is False and ref.ref not in effective["unpinned_templates"]:
                effective["unpinned_templates"].append(ref.ref)
            if not key:
                effective["unresolved_templates"].append(ref.ref)
                continue
            ref.resolved_key = key
            effective["template_edges"].append((model.key, key, ref.level))
            entry = templates_index.setdefault(key, {"key": key, "repo": repo, "path": path, "levels": set(),
                                                     "used_by": set(), "included_from": set()})
            entry["levels"].add(ref.level)
            entry["used_by"].add(pipeline["name"])
            entry["included_from"].add(model.key)
            if key not in effective["templates"]:
                effective["templates"].append(key)
            if key in visited or depth >= MAX_TEMPLATE_DEPTH:
                continue
            child = resolver.parse(key, ref.parameters)
            models.append(child)
            walk(child, depth + 1, visited + (key,))

    walk(root, 0, (root.key,))

    for m in models:
        for field_name in ("tasks", "pools", "scripts"):
            effective[field_name].extend(getattr(m, field_name))
        for field_name in ("environments", "checkouts", "key_vaults", "feeds", "variable_groups"):
            for value in getattr(m, field_name):
                if value not in effective[field_name]:
                    effective[field_name].append(value)
        for sc in m.service_connections:
            bucket = "dynamic_service_connections" if sc["dynamic"] else "service_connections"
            if sc["name"] not in effective[bucket]:
                effective[bucket].append(sc["name"])
        effective["continue_on_error"] += m.continue_on_error
        effective["persist_credentials"] |= m.persist_credentials
        if m is not root:
            effective["variables_inline"].extend(m.variables_inline)

    effective["stages"] = _effective_stages(root, resolver)
    return effective


def _include(ref: str, parameters: dict, level: str, including: FileModel, resolver: TemplateResolver,
             aliases: dict) -> FileModel | None:
    tref = TemplateRef(ref, level, parameters=parameters or {})
    repo, path, _ = resolve_template(tref, including, aliases)
    key = resolver.lookup(f"{repo}:{path}") if repo else None
    return resolver.parse(key, tref.parameters) if key else None


def _effective_stages(root: FileModel, resolver: TemplateResolver) -> list[dict]:
    """Stage flow with stage/job templates (and extends) expanded where they can be resolved."""
    aliases = {r["alias"]: r for r in root.repositories if r.get("alias")}
    including = root
    stages = list(root.stages)
    if root.extends and not stages:
        ext = next((t for t in root.templates if t.level == "extends"), None)
        if ext and ext.resolved_key:
            including = resolver.parse(ext.resolved_key, ext.parameters)
            stages = list(including.stages)
        if ext and (not stages or all(s["name"].startswith("template:") for s in stages)):
            for key in ("stages", "jobs", "steps"):
                if isinstance(ext.parameters.get(key), list):
                    stages, including = extract_stages({key: ext.parameters[key]}), root
                    break
    expanded: list[dict] = []
    for stage in stages:
        if stage.get("template"):
            child = _include(stage["template"], {}, "stage", including, resolver, aliases)
            if child and child.stages:
                expanded.extend(child.stages)
                continue
        jobs = []
        for job in stage.get("jobs", []):
            if job.get("kind") == "template":
                child = _include(job["template"], job.get("parameters", {}), "job", including, resolver, aliases)
                child_jobs = [j for s in (child.stages if child else []) for j in s.get("jobs", [])]
                if child_jobs:
                    jobs.extend(child_jobs)
                    continue
            jobs.append(job)
        expanded.append({**stage, "jobs": jobs})
    return expanded


@register_analyser("yaml", order=10)
class YamlAnalyser(Analyser):
    """Parse all YAML (pipelines, templates) and build the effective model of each YAML pipeline."""

    def analyse(self, ctx: RunContext) -> None:
        yaml_files = dict(ctx.snapshot.get("yaml_files") or {})
        expanded = ctx.snapshot.get("expanded_yaml") or {}
        resolver = TemplateResolver(yaml_files)
        templates_index: dict[str, dict] = {}
        pipelines_out: dict[str, dict] = {}
        root_keys = set()

        for pipeline in ctx.snapshot.get("pipelines", []):
            key = pipeline.get("yaml_key")
            if pipeline.get("type") != "yaml" or not key or key not in yaml_files:
                continue
            root_keys.add(key)
            root = resolver.parse(key)
            effective = build_effective(pipeline, root, resolver, templates_index)
            if str(pipeline["id"]) in expanded:  # server-side expansion is authoritative for stage flow
                full = parse_file(f"{key}#expanded", expanded[str(pipeline["id"])])
                if not full.error:
                    effective["stages"] = full.stages
                    for sc in full.service_connections:
                        if not sc["dynamic"] and sc["name"] not in effective["service_connections"]:
                            effective["service_connections"].append(sc["name"])
                    effective["expanded"] = True
            pipelines_out[str(pipeline["id"])] = effective

        for key in yaml_files:  # parse files not reached through any pipeline
            if key not in resolver.files:
                resolver.parse(key)

        template_keys = set(templates_index)
        unregistered = [
            key for key, m in resolver.files.items()
            if m.kind == "pipeline" and key not in root_keys and key not in template_keys
        ]
        for entry in templates_index.values():
            for k in ("levels", "used_by", "included_from"):
                entry[k] = sorted(entry[k])
        ctx.analysis["yaml"] = {
            "files": {k: m.to_dict() for k, m in resolver.files.items()},
            "pipelines": pipelines_out,
            "templates": templates_index,
            "unregistered_pipelines": sorted(unregistered),
            "parse_errors": {k: m.error for k, m in resolver.files.items() if m.error},
        }
