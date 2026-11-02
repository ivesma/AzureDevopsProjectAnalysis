"""Static parser for Azure Pipelines YAML.

The parser does not evaluate template expressions. Instead it takes the *union* of every
conditional branch (``${{ if }}``, ``${{ else }}``, ``${{ each }}``), which is what you want for
dependency analysis: anything a pipeline *could* reference is reported.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import yaml

EXPR_KEY = re.compile(r"^\$\{\{.*\}\}$", re.S)
PARAM_REF = re.compile(r"^\$\{\{\s*parameters\.([A-Za-z0-9_]+)\s*\}\}$")
MACRO_RE = re.compile(r"\$\(([A-Za-z0-9_.]+)\)")

SERVICE_CONNECTION_INPUTS = {
    "azuresubscription", "connectedservicename", "connectedservicenamearm", "azureresourcemanagerconnection",
    "kubernetesserviceconnection", "kubernetesserviceendpoint", "dockerregistryserviceconnection",
    "containerregistry", "dockerregistryendpoint", "serviceconnection", "externalfeedcredentials",
    "githubconnection", "gitconnection", "sshendpoint", "nugetserviceconnection", "serviceendpoint",
    "azuresubscriptionendpoint", "azureserviceconnection", "backendservicearm", "environmentservicenameazurerm",
    "sonarqube", "sonarcloud", "artifactoryservice", "awscredentials", "publishendpoint", "connection",
    "azurecontainerregistry", "helmserviceconnection", "terraformserviceconnection",
}
FEED_INPUTS = {"vstsfeed", "vstsfeedpublish", "feedpublish", "publishfeed", "publishvstsfeed", "feed",
               "artifactfeeds", "internalfeed", "vstsfeedtopublish"}
SECRET_NAME_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|connectionstring|private[_-]?key|pwd)")
SCRIPT_KEYS = ("script", "bash", "pwsh", "powershell")


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates duplicate keys (last wins) and unknown tags."""


def _unknown_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_Loader.add_multi_constructor("!", _unknown_tag)
# Treat on/off/yes/no as strings like Azure Pipelines does (YAML 1.2 booleans only).
_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                              list("tTfF"))


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - restricted SafeLoader subclass


def flatten(obj: Any) -> Any:
    """Splice template-expression branches into their parent (union of all branches)."""
    if isinstance(obj, list):
        out: list[Any] = []
        for item in obj:
            if isinstance(item, dict) and item and all(isinstance(k, str) and EXPR_KEY.match(k) for k in item):
                for value in item.values():
                    value = flatten(value)
                    if isinstance(value, list):
                        out.extend(value)
                    elif value is not None:
                        out.append(value)
            else:
                out.append(flatten(item))
        return out
    if isinstance(obj, dict):
        out_d: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and EXPR_KEY.match(key):
                value = flatten(value)
                if isinstance(value, dict):
                    for k2, v2 in value.items():
                        out_d.setdefault(k2, v2)
                continue
            out_d[key] = flatten(value)
        return out_d
    return obj


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def split_template_ref(ref: str) -> tuple[str, str | None]:
    if "@" in ref:
        path, alias = ref.rsplit("@", 1)
        return path.strip(), alias.strip()
    return ref.strip(), None


@dataclass
class TemplateRef:
    ref: str
    level: str  # extends | stage | job | step | variables | unknown
    resolved_key: str | None = None
    repo: str | None = None
    path: str | None = None
    pinned: bool | None = None  # resources.repositories ref set for cross-repo templates
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileModel:
    key: str
    repo: str
    path: str
    error: str | None = None
    kind: str = "unknown"  # pipeline | template | manifest | other
    raw: Any = None
    trigger: Any = "default"
    pr: Any = "default"
    schedules: list[dict] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    variables_inline: list[dict] = field(default_factory=list)
    variable_groups: list[str] = field(default_factory=list)
    templates: list[TemplateRef] = field(default_factory=list)
    repositories: list[dict] = field(default_factory=list)
    pipeline_resources: list[dict] = field(default_factory=list)
    containers: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    service_connections: list[dict] = field(default_factory=list)
    environments: list[str] = field(default_factory=list)
    pools: list[dict] = field(default_factory=list)
    checkouts: list[str] = field(default_factory=list)
    key_vaults: list[str] = field(default_factory=list)
    feeds: list[str] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)
    continue_on_error: int = 0
    persist_credentials: bool = False
    extends: str | None = None
    has_stages: bool = False
    has_jobs: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "raw"}
        data["templates"] = [t.__dict__ for t in self.templates]
        return data


def _resolve_param(value: Any, params: dict[str, Any]) -> tuple[Any, bool]:
    """Resolve '${{ parameters.x }}' using known parameter values. Returns (value, dynamic)."""
    if isinstance(value, str):
        match = PARAM_REF.match(value.strip())
        if match:
            name = match.group(1)
            if name in params and isinstance(params[name], (str, int, float)):
                return str(params[name]), False
            return value, True
        return value, "$(" in value or "${{" in value
    return value, False


def _walk(obj: Any, visit: Callable[[dict, str], None], parent_key: str = "") -> None:
    if isinstance(obj, dict):
        visit(obj, parent_key)
        for key, value in obj.items():
            _walk(value, visit, str(key))
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, visit, parent_key)


_LEVEL_BY_PARENT = {"stages": "stage", "jobs": "job", "steps": "step", "variables": "variables",
                    "deploy": "step", "preDeploy": "step", "routeTraffic": "step", "postRouteTraffic": "step",
                    "success": "step", "failure": "step"}


def parse_file(key: str, text: str, params: dict[str, Any] | None = None) -> FileModel:
    repo, _, path = key.partition(":")
    model = FileModel(key=key, repo=repo, path=path)
    params = dict(params or {})
    try:
        doc = load_yaml(text)
    except yaml.YAMLError as exc:
        model.error = f"YAML parse error: {str(exc).splitlines()[0]}"
        return model
    if not isinstance(doc, dict):
        model.kind = "other"
        model.raw = doc
        return model
    if "apiVersion" in doc and "kind" in doc:
        model.kind = "manifest"
        return model

    for p in as_list(doc.get("parameters")):
        if isinstance(p, dict) and "name" in p:
            model.parameters.append({"name": p.get("name"), "type": p.get("type", "string"),
                                     "default": p.get("default"), "values": p.get("values")})
            params.setdefault(p["name"], p.get("default"))
        elif isinstance(doc.get("parameters"), dict):  # old-style template parameters mapping
            break
    if isinstance(doc.get("parameters"), dict):
        for name, default in doc["parameters"].items():
            model.parameters.append({"name": name, "type": "object", "default": default, "values": None})
            params.setdefault(name, default)

    doc = flatten(doc)
    model.raw = doc
    model.trigger = doc.get("trigger", "default")
    model.pr = doc.get("pr", "default")
    model.has_stages = "stages" in doc
    model.has_jobs = "jobs" in doc
    for sched in as_list(doc.get("schedules")):
        if isinstance(sched, dict):
            branches = sched.get("branches") or {}
            model.schedules.append({
                "cron": sched.get("cron"), "name": sched.get("displayName"),
                "branches": as_list(branches.get("include") if isinstance(branches, dict) else branches),
                "always": bool(sched.get("always")),
            })
    _variables(doc.get("variables"), model)

    resources = doc.get("resources") or {}
    if isinstance(resources, dict):
        for r in as_list(resources.get("repositories")):
            if isinstance(r, dict):
                model.repositories.append({"alias": r.get("repository"), "type": r.get("type", "git"),
                                           "name": r.get("name"), "ref": r.get("ref"), "endpoint": r.get("endpoint")})
                if r.get("endpoint"):
                    model.service_connections.append({"name": r["endpoint"], "input": "resources.repositories.endpoint",
                                                      "task": None, "dynamic": False})
        for r in as_list(resources.get("pipelines")):
            if isinstance(r, dict):
                trig = r.get("trigger")
                model.pipeline_resources.append({"alias": r.get("pipeline"), "source": r.get("source"),
                                                 "project": r.get("project"), "trigger": trig is not None and trig != "none"
                                                 and trig is not False})
        for r in as_list(resources.get("containers")):
            if isinstance(r, dict):
                model.containers.append({"alias": r.get("container"), "image": r.get("image"),
                                         "endpoint": r.get("endpoint")})
                if r.get("endpoint"):
                    model.service_connections.append({"name": r["endpoint"], "input": "resources.containers.endpoint",
                                                      "task": None, "dynamic": False})

    if isinstance(doc.get("extends"), dict) and doc["extends"].get("template"):
        model.extends = str(doc["extends"]["template"])
        model.templates.append(TemplateRef(model.extends, "extends",
                                           parameters=doc["extends"].get("parameters") or {}))

    model.stages = extract_stages(doc, params)
    _deep_scan(doc, model, params)

    if model.extends or any(k in doc for k in ("trigger", "pr", "schedules", "resources", "pool")) \
            or (doc.get("stages") and not model.parameters) or doc.get("name"):
        model.kind = "pipeline"
    elif any(k in doc for k in ("stages", "jobs", "steps", "variables", "parameters")):
        model.kind = "template"
    else:
        model.kind = "other"
    return model


def _variables(value: Any, model: FileModel) -> None:
    if isinstance(value, dict):
        for name, val in value.items():
            model.variables_inline.append(_inline_var(str(name), val))
        return
    for item in as_list(value):
        if not isinstance(item, dict):
            continue
        if "group" in item:
            model.variable_groups.append(str(item["group"]))
        elif "name" in item:
            model.variables_inline.append(_inline_var(str(item["name"]), item.get("value")))


def _inline_var(name: str, value: Any) -> dict:
    text = "" if value is None else str(value)
    literal = bool(text) and "$(" not in text and "${{" not in text and "$[" not in text
    return {"name": name, "value": None if SECRET_NAME_RE.search(name) else text[:200],
            "looks_secret": bool(SECRET_NAME_RE.search(name)) and literal and len(text) >= 6}


def _template_key(ref: str, parameters: Any) -> tuple[str, str]:
    return ref, repr(sorted(parameters.items(), key=lambda kv: str(kv[0]))) if isinstance(parameters, dict) else ""


def _deep_scan(doc: Any, model: FileModel, params: dict[str, Any]) -> None:
    # The same template included with different parameters (e.g. one deploy template per stage) is a
    # separate inclusion - its parameters may resolve to different service connections/environments.
    seen_templates = {_template_key(t.ref, t.parameters) for t in model.templates}

    def visit(d: dict, parent_key: str) -> None:
        if "task" in d and isinstance(d["task"], str):
            name, _, version = d["task"].partition("@")
            inputs = d.get("inputs") or {}
            model.tasks.append({"task": name.strip(), "version": version.strip() or None,
                                "display": d.get("displayName"), "enabled": d.get("enabled", True) is not False})
            if isinstance(inputs, dict):
                for key, val in inputs.items():
                    lk = str(key).lower()
                    if lk in SERVICE_CONNECTION_INPUTS and val not in (None, ""):
                        resolved, dynamic = _resolve_param(val, params)
                        for single in str(resolved).split(","):
                            if single.strip():
                                model.service_connections.append({"name": single.strip(), "input": key,
                                                                  "task": name.strip(), "dynamic": dynamic})
                    if lk in FEED_INPUTS and isinstance(val, str) and val and val not in ("select", "config"):
                        model.feeds.append(_resolve_param(val, params)[0])
                    if lk == "keyvaultname" and val:
                        model.key_vaults.append(str(_resolve_param(val, params)[0]))
        for skey in SCRIPT_KEYS:
            if skey in d and isinstance(d[skey], str):
                model.scripts.append({"kind": skey, "text": d[skey][:4000], "env": sorted((d.get("env") or {}).keys())
                                      if isinstance(d.get("env"), dict) else []})
        if d.get("continueOnError") in (True, "true"):
            model.continue_on_error += 1
        if "checkout" in d:
            model.checkouts.append(str(d["checkout"]))
            if d.get("persistCredentials") in (True, "true"):
                model.persist_credentials = True
        if "template" in d and isinstance(d["template"], str) and parent_key != "extends":
            ref = d["template"]
            key = _template_key(ref, d.get("parameters") or {})
            if key not in seen_templates:
                seen_templates.add(key)
                model.templates.append(TemplateRef(ref, _LEVEL_BY_PARENT.get(parent_key, "unknown"),
                                                   parameters=d.get("parameters") or {}))
        if "environment" in d and ("deployment" in d or parent_key == "jobs"):
            env = d["environment"]
            env_name = env.get("name") if isinstance(env, dict) else env
            if env_name:
                env_name = str(_resolve_param(env_name, params)[0]).split(".")[0]
                if env_name not in model.environments:
                    model.environments.append(env_name)
        if "pool" in d:
            pool = d["pool"]
            if isinstance(pool, str):
                entry = {"name": pool, "vm_image": None, "demands": []}
            elif isinstance(pool, dict):
                entry = {"name": pool.get("name"), "vm_image": pool.get("vmImage"),
                         "demands": [str(x) for x in as_list(pool.get("demands"))]}
            else:
                entry = None
            if entry:
                entry["name"], _ = _resolve_param(entry["name"], params) if entry["name"] else (None, False)
                if entry not in model.pools:
                    model.pools.append(entry)
        if parent_key == "variables" and "group" in d and str(d["group"]) not in model.variable_groups:
            model.variable_groups.append(str(_resolve_param(d["group"], params)[0]))

    _walk(doc, visit)


def _depends(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in as_list(value)]


def extract_stages(doc: dict, params: dict[str, Any] | None = None) -> list[dict]:
    """Return stage -> jobs structure. Pipelines without stages get one implicit stage."""
    params = params or {}
    stages = []
    raw_stages = as_list(doc.get("stages"))
    if not raw_stages and (doc.get("jobs") or doc.get("steps")):
        raw_stages = [{"stage": "__default", "jobs": doc.get("jobs") or [{"job": "__default",
                                                                         "steps": doc.get("steps")}]}]
    for index, st in enumerate(raw_stages):
        if not isinstance(st, dict):
            continue
        if "template" in st and "stage" not in st:
            stages.append({"name": f"template:{st['template']}", "template": st["template"], "depends_on": None,
                           "jobs": [], "condition": None, "index": index})
            continue
        jobs = []
        for job in as_list(st.get("jobs")):
            if not isinstance(job, dict):
                continue
            if "template" in job and "job" not in job and "deployment" not in job:
                jobs.append({"name": f"template:{job['template']}", "kind": "template", "environment": None,
                             "depends_on": None, "template": job["template"],
                             "parameters": job.get("parameters") or {}})
                continue
            env = job.get("environment")
            env = env.get("name") if isinstance(env, dict) else env
            if env is not None:
                env = str(_resolve_param(env, params)[0]).split(".")[0]
            jobs.append({
                "name": str(job.get("job") or job.get("deployment") or "job"),
                "kind": "deployment" if "deployment" in job else "job",
                "environment": env,
                "depends_on": _depends(job.get("dependsOn")),
                "condition": job.get("condition"),
                "timeout": job.get("timeoutInMinutes"),
                "strategy": next(iter((job.get("strategy") or {}).keys()), None) if isinstance(job.get("strategy"), dict)
                else None,
                "step_count": _count_steps(job),
            })
        stages.append({
            "name": str(st.get("stage") or f"stage{index + 1}"),
            "display_name": st.get("displayName"),
            "depends_on": _depends(st.get("dependsOn")),
            "condition": st.get("condition"),
            "jobs": jobs,
            "template": None,
            "index": index,
        })
    return stages


def _count_steps(job: dict) -> int:
    count = 0

    def visit(d: dict, parent: str) -> None:
        nonlocal count
        if any(k in d for k in ("task", "script", "bash", "pwsh", "powershell", "checkout", "download", "publish")) \
                or (parent == "steps" and "template" in d):
            count += 1

    _walk(job, visit)
    return count


def resolve_template(ref: TemplateRef, including: FileModel, aliases: dict[str, dict]) -> tuple[str | None, str, bool | None]:
    """Resolve a template reference to a 'repo:/path' key.

    ``aliases`` maps resource repository aliases (from the *root pipeline*) to their definition.
    Returns (repo_name, path, pinned).
    """
    path, alias = split_template_ref(ref.ref)
    if "${{" in path:
        return None, path, None
    pinned: bool | None = None
    if alias and alias != "self":
        resource = aliases.get(alias)
        if not resource:
            return None, path, None
        repo_name = str(resource.get("name") or alias).split("/")[-1]
        # Only tags are immutable; branch refs (or no ref = default branch) can change under the pipeline.
        pinned = str(resource.get("ref") or "").startswith("refs/tags/")
        full = "/" + path.lstrip("/")
        return repo_name, posixpath.normpath(full), pinned
    if path.startswith("/"):
        return including.repo, posixpath.normpath(path), None
    base = posixpath.dirname(including.path) or "/"
    return including.repo, posixpath.normpath(posixpath.join(base, path)), None


def placeholder_expressions(text: str) -> list[str]:
    return MACRO_RE.findall(text or "")
