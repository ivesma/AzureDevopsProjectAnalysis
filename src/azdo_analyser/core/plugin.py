"""Plugin registry.

Collectors, analysers and exporters register themselves with a decorator. Third-party packages
can contribute plugins through the ``azdo_analyser.plugins`` entry-point group: each entry point
should reference a module (or callable) that registers plugins when imported/called.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

KINDS = ("collector", "analyser", "exporter")


@dataclass
class PluginSpec:
    kind: str
    name: str
    factory: Callable[..., Any]
    order: int
    description: str
    depends_on: tuple[str, ...] = ()


_REGISTRY: dict[str, dict[str, PluginSpec]] = {k: {} for k in KINDS}
_BUILTINS_LOADED = False

BUILTIN_MODULES = [
    "azdo_analyser.collectors.project",
    "azdo_analyser.collectors.repositories",
    "azdo_analyser.collectors.pipelines",
    "azdo_analyser.collectors.releases",
    "azdo_analyser.collectors.environments",
    "azdo_analyser.collectors.agent_pools",
    "azdo_analyser.collectors.library",
    "azdo_analyser.collectors.service_connections",
    "azdo_analyser.collectors.boards",
    "azdo_analyser.collectors.wiki",
    "azdo_analyser.collectors.test_plans",
    "azdo_analyser.collectors.artifacts",
    "azdo_analyser.collectors.security",
    "azdo_analyser.analysers.yaml_analysis",
    "azdo_analyser.analysers.pipeline_graphs",
    "azdo_analyser.analysers.dependency_engine",
    "azdo_analyser.analysers.hierarchy_graphs",
    "azdo_analyser.analysers.metrics",
    "azdo_analyser.analysers.scoring",
    "azdo_analyser.analysers.risk_engine",
    "azdo_analyser.analysers.trends",
    "azdo_analyser.analysers.summary",
    "azdo_analyser.exporters.json_export",
    "azdo_analyser.exporters.mermaid_export",
    "azdo_analyser.exporters.excel",
    "azdo_analyser.exporters.markdown",
    "azdo_analyser.exporters.html",
    "azdo_analyser.exporters.wiki",
    "azdo_analyser.exporters.powerbi",
]


def register(kind: str, name: str, order: int = 100, description: str = "",
             depends_on: tuple[str, ...] = ()) -> Callable[[T], T]:
    if kind not in KINDS:
        raise ValueError(f"Unknown plugin kind {kind!r}")

    def decorator(factory: T) -> T:
        _REGISTRY[kind][name] = PluginSpec(kind, name, factory, order,
                                           description or (factory.__doc__ or "").strip().split("\n")[0],
                                           tuple(depends_on))
        return factory

    return decorator


def register_collector(name: str, order: int = 100, **kw) -> Callable[[T], T]:
    return register("collector", name, order, **kw)


def register_analyser(name: str, order: int = 100, **kw) -> Callable[[T], T]:
    return register("analyser", name, order, **kw)


def register_exporter(name: str, order: int = 100, **kw) -> Callable[[T], T]:
    return register("exporter", name, order, **kw)


def load_plugins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
    try:
        eps = metadata.entry_points(group="azdo_analyser.plugins")
    except Exception:  # pragma: no cover - defensive
        eps = []
    for ep in eps:  # pragma: no cover - depends on installed third-party packages
        try:
            loaded = ep.load()
            if callable(loaded):
                loaded()
        except Exception as exc:
            log.warning("Failed to load plugin %s: %s", ep.name, exc)
    _BUILTINS_LOADED = True


def plugins(kind: str) -> list[PluginSpec]:
    load_plugins()
    specs = sorted(_REGISTRY[kind].values(), key=lambda s: (s.order, s.name))
    return _topological(specs)


def _topological(specs: list[PluginSpec]) -> list[PluginSpec]:
    by_name = {s.name: s for s in specs}
    ordered: list[PluginSpec] = []
    seen: set[str] = set()

    def visit(spec: PluginSpec, stack: tuple[str, ...] = ()) -> None:
        if spec.name in seen:
            return
        if spec.name in stack:
            raise ValueError(f"Plugin dependency cycle: {' -> '.join(stack + (spec.name,))}")
        for dep in spec.depends_on:
            if dep in by_name:
                visit(by_name[dep], stack + (spec.name,))
        seen.add(spec.name)
        ordered.append(spec)

    for spec in specs:
        visit(spec)
    return ordered
