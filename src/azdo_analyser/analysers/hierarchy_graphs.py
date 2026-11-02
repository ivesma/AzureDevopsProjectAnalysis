"""Hierarchy diagrams: work item type hierarchy, wiki site maps and security group structure."""

from __future__ import annotations

from collections import Counter

from azdo_analyser.collectors.wiki import normalise_page_path
from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Graph
from azdo_analyser.core.plugin import register_analyser

MAX_WIKI_NODES = 150


def work_item_hierarchy(boards: dict) -> Graph:
    graph = Graph("work-item-hierarchy", "Work item hierarchy", "boards", direction="TD",
                  description="Backlog levels and observed parent/child relationships with item counts")
    items = boards.get("work_items", [])
    counts = Counter(w["type"] for w in items)
    by_id = {w["id"]: w for w in items}
    levels = boards.get("backlog_hierarchy", [])
    previous: list[str] = []
    for level in levels:
        current = []
        for wtype in level["types"]:
            node = graph.add_node(f"t:{wtype}", f"{wtype}\n{counts.get(wtype, 0)} items", "work_item_type",
                                  group=level["name"])
            current.append(node)
            for parent in previous:
                graph.add_edge(parent, node, None, "dashed")
        previous = current or previous
    observed = Counter()
    for w in items:
        parent = by_id.get(w.get("parent"))
        if parent:
            observed[(parent["type"], w["type"])] += 1
    if observed:  # real parent/child links replace the generic backlog-level edges
        graph.edges = []
    for (ptype, ctype), n in observed.items():
        graph.add_node(f"t:{ptype}", f"{ptype}\n{counts.get(ptype, 0)} items", "work_item_type")
        graph.add_node(f"t:{ctype}", f"{ctype}\n{counts.get(ctype, 0)} items", "work_item_type")
        graph.add_edge(f"t:{ptype}", f"t:{ctype}", f"{n} links")
    for wtype in counts:
        graph.add_node(f"t:{wtype}", f"{wtype}\n{counts[wtype]} items", "work_item_type")
    return graph


def wiki_sitemap(wiki: dict, broken: dict[str, list[str]], orphans: set[str]) -> Graph:
    graph = Graph(f"wiki-sitemap-{wiki['name']}", f"Wiki site map: {wiki['name']}", "wiki", direction="LR",
                  description="Page hierarchy (solid), broken links (red) and orphaned pages")
    root = graph.add_node("root", wiki["name"], "start")
    pages = wiki.get("pages", [])[:MAX_WIKI_NODES]
    known = {p["path"] for p in pages}
    for page in pages:
        path = page["path"]
        graph.add_node(path, path.rsplit("/", 1)[-1] or path, "orphan" if path in orphans else "page")
        parent = path.rsplit("/", 1)[0]
        graph.add_edge(parent if parent in known else root, path)
    for path, links in broken.items():
        if path not in known:
            continue
        for link in links:
            target = graph.add_node(f"missing:{link}", f"{link} (missing)", "missing")
            graph.add_edge(path, target, "broken link", "dashed")
    if len(wiki.get("pages", [])) > MAX_WIKI_NODES:
        graph.add_node("more", f"... {len(wiki['pages']) - MAX_WIKI_NODES} more pages", "default")
        graph.add_edge(root, "more", None, "dashed")
    return graph


def analyse_wiki_links(wiki: dict) -> tuple[dict[str, list[str]], set[str], dict[str, int]]:
    """Return (broken links by page, orphaned pages, inbound link counts)."""
    pages = wiki.get("pages", [])
    norm = {normalise_page_path(p["path"]): p["path"] for p in pages}
    inbound: Counter[str] = Counter()
    broken: dict[str, list[str]] = {}
    for page in pages:
        for link in page.get("links", []):
            if link in norm:
                if norm[link] != page["path"]:
                    inbound[norm[link]] += 1
            else:
                broken.setdefault(page["path"], []).append(link)
    scanned = any(p.get("content_scanned") for p in pages)
    orphans = set()
    if scanned:
        for page in pages:
            views = page.get("view_count_30d")
            if (page.get("depth", 0) > 0 and not page.get("is_parent") and inbound[page["path"]] == 0
                    and (views is None or views == 0)):
                orphans.add(page["path"])
    return broken, orphans, dict(inbound)


def security_graph(security: dict) -> Graph:
    graph = Graph("security-groups", "Security groups & membership", "security", direction="LR",
                  description="Project groups, nested groups and member type counts")
    groups = security.get("groups", [])
    by_descriptor = {g["descriptor"]: g for g in groups}
    for g in groups:
        kinds = Counter(m["kind"] for m in g["members"])
        summary = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(kinds.items())) or "empty"
        graph.add_node(g["descriptor"], f"{g['display_name']}\n{summary}",
                       "admin_group" if g["display_name"] == "Project Administrators" else "group")
    for g in groups:
        for m in g["members"]:
            if m["descriptor"] in by_descriptor:
                graph.add_edge(m["descriptor"], g["descriptor"], "member of")
            elif m["kind"] in ("service_principal", "build_identity"):
                node = graph.add_node(m["descriptor"], m["name"], "service_identity")
                graph.add_edge(node, g["descriptor"], "member of", "dashed")
    return graph


@register_analyser("hierarchies", order=35)
class HierarchyAnalyser(Analyser):
    """Work item hierarchy, wiki site maps (with broken/orphan detection) and security group diagrams."""

    def analyse(self, ctx: RunContext) -> None:
        boards = ctx.snapshot.get("boards")
        if boards and (boards.get("work_items") or boards.get("backlog_hierarchy")):
            ctx.assessment.add_graph(work_item_hierarchy(boards))
        wiki_results = []
        for wiki in (ctx.snapshot.get("wiki") or {}).get("wikis", []):
            broken, orphans, inbound = analyse_wiki_links(wiki)
            wiki_results.append({"wiki": wiki["name"], "broken_links": broken, "orphans": sorted(orphans),
                                 "inbound": inbound, "page_count": len(wiki.get("pages", []))})
            if wiki.get("pages"):
                ctx.assessment.add_graph(wiki_sitemap(wiki, broken, orphans))
        ctx.analysis["wiki"] = wiki_results
        security = ctx.snapshot.get("security")
        if security and security.get("groups"):
            ctx.assessment.add_graph(security_graph(security))
