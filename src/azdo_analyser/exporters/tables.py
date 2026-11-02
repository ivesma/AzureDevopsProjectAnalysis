"""Flat, pivot-ready tables derived from the assessment, shared by Excel/HTML/Markdown/Power BI exporters."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from azdo_analyser.core.context import RunContext
from azdo_analyser.core.utils import days_between, parse_date

Rows = list[dict[str, Any]]


def dt(value: Any) -> datetime | None:
    """Timezone-naive UTC datetime (Excel cannot store tz-aware values)."""
    parsed = parse_date(value)
    return parsed.replace(tzinfo=None) if parsed else None


def joined(values: Any, sep: str = ", ") -> str:
    if not values:
        return ""
    return sep.join(str(v) for v in values if v not in (None, ""))


def _days(value: Any, ctx: RunContext) -> int | None:
    d = days_between(value, ctx.now)
    return int(d) if d is not None else None


def summary(ctx: RunContext) -> Rows:
    a = ctx.assessment
    project = ctx.snapshot.get("project") or {}
    risk = ctx.analysis.get("risk") or {}
    debt = ctx.analysis.get("technical_debt") or {}
    inv = (ctx.analysis.get("metrics") or {}).get("inventory") or {}
    counts = a.severity_counts()
    rows = [
        ("Organisation", project.get("organisation") or a.organisation),
        ("Project", project.get("name") or a.project),
        ("Description", project.get("description")),
        ("Visibility", project.get("visibility")),
        ("Process template", project.get("process_template")),
        ("Last updated", dt(project.get("last_update_time"))),
        ("Default team", project.get("default_team")),
        ("Teams", len(project.get("teams", []))),
        ("Project administrators", joined((ctx.snapshot.get("security") or {}).get("project_admins", []))),
        ("Assessment generated (UTC)", a.generated_at.replace(tzinfo=None)),
        ("Analysis window (days)", ctx.settings.history_days),
        ("Risk score (0-100, higher = riskier)", risk.get("score")),
        ("Risk grade", risk.get("grade")),
        ("Technical debt score", debt.get("score")),
        ("Technical debt grade", debt.get("grade")),
        ("Findings - critical", counts["CRITICAL"]),
        ("Findings - high", counts["HIGH"]),
        ("Findings - medium", counts["MEDIUM"]),
        ("Findings - low", counts["LOW"]),
        ("Findings - info", counts["INFO"]),
    ]
    rows += [(f"Inventory - {k.replace('_', ' ')}", v) for k, v in inv.items()]
    return [{"Item": k, "Value": v} for k, v in rows]


def repositories(ctx: RunContext) -> Rows:
    out = []
    for r in ctx.snapshot.get("repositories", []):
        pol = r.get("default_branch_policies") or {}
        prs = r.get("pull_requests") or {}
        out.append({
            "Repository": r["name"],
            "Default Branch": r.get("default_branch"),
            "Disabled": r.get("is_disabled"),
            "Empty": r.get("is_empty"),
            "Fork": r.get("is_fork"),
            "Size (MB)": round((r.get("size_bytes") or 0) / 1048576, 2),
            "Branches": r.get("branch_count"),
            "Locked Branches": joined(r.get("locked_branches")),
            "Last Commit": dt(r.get("last_commit_date")),
            "Days Since Commit": _days(r.get("last_commit_date"), ctx),
            "Commits (window)": r.get("commits_in_window"),
            "Contributors (window)": r.get("contributor_count"),
            "Top Contributors": joined(c["name"] for c in r.get("contributors", [])[:5]),
            "PRs Total": prs.get("total"),
            "PRs Active": prs.get("active"),
            "PRs Completed (window)": prs.get("completed_in_window"),
            "Avg PR Completion (h)": prs.get("avg_completion_hours"),
            "Merge Strategies Used": joined(f"{k}:{v}" for k, v in (prs.get("merge_strategies_used") or {}).items()),
            "Branch Policies": pol.get("count"),
            "Blocking Policies": pol.get("blocking_count"),
            "Min Reviewers": pol.get("min_reviewers"),
            "Creator Vote Counts": pol.get("creator_vote_counts"),
            "Build Validation": pol.get("build_validation"),
            "Comment Resolution": pol.get("comment_resolution"),
            "Work Item Linking": pol.get("work_item_linking"),
            "Allowed Merge Types": joined(pol.get("merge_strategies")),
            "Technologies": joined(r.get("technologies")),
            "YAML Files": len(r.get("yaml_files", [])),
            "Submodules": len(r.get("submodules", [])),
            "URL": r.get("web_url"),
        })
    return out


def pipelines(ctx: RunContext) -> Rows:
    effective = ctx.analysis.get("yaml", {}).get("pipelines", {})
    complexity = ctx.analysis.get("complexity", {})
    out = []
    for p in ctx.snapshot.get("pipelines", []):
        m = effective.get(str(p["id"])) or {}
        runs = p.get("runs") or {}
        c = complexity.get(p["name"], {})
        triggers = [t["type"] for t in p.get("triggers", [])]
        if m:
            if m.get("trigger") not in ("default", None):
                triggers.append("YAML CI" if m["trigger"] != "none" else "YAML CI: none")
            if m.get("schedules"):
                triggers.append("YAML schedule")
            triggers += [f"pipeline:{r['source']}" for r in m.get("pipeline_resources", []) if r.get("trigger")]
        pools = {x.get("name") or f"Azure Pipelines ({x.get('vm_image')})" for x in m.get("pools", [])} or (
            {p["pool"]} if p.get("pool") else set())
        out.append({
            "Pipeline": p["name"],
            "ID": p["id"],
            "Folder": p.get("folder"),
            "Type": p["type"],
            "Repository": (p.get("repository") or {}).get("name"),
            "Repository Type": (p.get("repository") or {}).get("type"),
            "YAML Path": p.get("yaml_path"),
            "YAML Status": p.get("yaml_status"),
            "Queue Status": p.get("queue_status"),
            "Triggers": joined(dict.fromkeys(triggers)),
            "Schedules": len(p.get("schedules", [])) + len(m.get("schedules", [])),
            "Variables": len(p.get("variables", [])) + len(m.get("variables_inline", [])),
            "Secret Variables": sum(1 for v in p.get("variables", []) if v["is_secret"]),
            "Variable Groups": joined(dict.fromkeys(p.get("variable_groups", []) + m.get("variable_groups", []))),
            "Templates": len(m.get("templates", [])),
            "Extends": m.get("extends"),
            "Runtime Parameters": joined(x["name"] for x in m.get("parameters", [])),
            "Service Connections": joined(m.get("service_connections", [])),
            "Dynamic Service Connections": joined(m.get("dynamic_service_connections", [])),
            "Agent Pools": joined(sorted(pools)),
            "Demands": joined(p.get("demands")),
            "Environments": joined(m.get("environments", [])),
            "Stages": c.get("stages"),
            "Jobs": c.get("jobs"),
            "Steps": c.get("steps"),
            "Complexity Score": c.get("score"),
            "Complexity": c.get("band"),
            "Runs (window)": runs.get("total"),
            "Success Rate": runs.get("success_rate"),
            "Last Run": dt(runs.get("last_run")),
            "Last Result": runs.get("last_result"),
            "Avg Duration (min)": runs.get("avg_duration_min"),
            "Author": p.get("author"),
            "Created": dt(p.get("created_date")),
            "URL": p.get("web_url"),
        })
    return out


def pipeline_dependencies(ctx: RunContext) -> Rows:
    return [{"Source": d["source"], "Source Type": d["source_type"], "Relation": d["relation"],
             "Target": d["target"], "Target Type": d["target_type"]}
            for d in ctx.analysis.get("pipeline_dependencies", [])]


def templates(ctx: RunContext) -> Rows:
    return [{"Template": t["path"], "Repository": t["repo"], "Levels": joined(t["levels"]),
             "Used By (count)": len(t["used_by"]), "Used By": joined(t["used_by"]),
             "Included From": joined(x.split(":", 1)[-1] for x in t["included_from"])}
            for t in ctx.analysis.get("yaml", {}).get("templates", {}).values()]


def variables(ctx: RunContext) -> Rows:
    out = []
    effective = ctx.analysis.get("yaml", {}).get("pipelines", {})
    for p in ctx.snapshot.get("pipelines", []):
        for v in p.get("variables", []):
            out.append({"Owner": p["name"], "Owner Type": "pipeline", "Variable": v["name"], "Secret": v["is_secret"],
                        "Source": "pipeline definition", "Settable at Queue Time": v.get("allow_override"),
                        "Value": v.get("value")})
        for v in (effective.get(str(p["id"])) or {}).get("variables_inline", []):
            out.append({"Owner": p["name"], "Owner Type": "pipeline", "Variable": v["name"],
                        "Secret": bool(v.get("looks_secret")), "Source": "YAML",
                        "Settable at Queue Time": None, "Value": v.get("value")})
    for r in ctx.snapshot.get("release_pipelines", []):
        for v in r.get("variables", []):
            out.append({"Owner": r["name"], "Owner Type": "release", "Variable": v["name"], "Secret": v["is_secret"],
                        "Source": "release definition", "Settable at Queue Time": None, "Value": None})
        for s in r.get("stages", []):
            for v in s.get("variables", []):
                out.append({"Owner": f"{r['name']} / {s['name']}", "Owner Type": "release stage", "Variable": v["name"],
                            "Secret": v["is_secret"], "Source": "stage", "Settable at Queue Time": None, "Value": None})
    for g in ctx.snapshot.get("variable_groups", []):
        for v in g.get("variables", []):
            out.append({"Owner": g["name"], "Owner Type": "variable group", "Variable": v["name"], "Secret": v["is_secret"],
                        "Source": f"Key Vault {g['key_vault']}" if g.get("key_vault") else "variable group",
                        "Settable at Queue Time": None, "Value": None})
    return out


def variable_groups(ctx: RunContext) -> Rows:
    usage = ctx.analysis.get("variable_group_usage", [])
    out = []
    for g in ctx.snapshot.get("variable_groups", []):
        consumers = sorted({u["consumer"] for u in usage if u["resource"] == g["name"]})
        out.append({
            "Variable Group": g["name"], "Type": g.get("type"), "Key Vault": g.get("key_vault"),
            "Variables": g.get("variable_count"), "Secrets": g.get("secret_count"),
            "Open Access": g.get("open_access"), "Authorised Pipelines": len(g.get("authorized_pipelines", [])),
            "Checks": joined(c["type"] for c in g.get("checks", [])),
            "Consumers (count)": len(consumers), "Consumers": joined(consumers), "Unused": not consumers,
            "Created By": g.get("created_by"), "Modified By": g.get("modified_by"), "Modified": dt(g.get("modified_on")),
            "Description": g.get("description"),
        })
    return out


def variable_matrix(ctx: RunContext) -> Rows:
    """Pipeline x variable group usage matrix (1 = uses)."""
    usage = ctx.analysis.get("variable_group_usage", [])
    groups = sorted({u["resource"] for u in usage})
    consumers = sorted({u["consumer"] for u in usage})
    out = []
    for consumer in consumers:
        row: dict[str, Any] = {"Consumer": consumer}
        for g in groups:
            row[g] = 1 if any(u["consumer"] == consumer and u["resource"] == g for u in usage) else None
        out.append(row)
    return out


def service_connections(ctx: RunContext) -> Rows:
    usage = ctx.analysis.get("service_connection_usage", [])
    out = []
    for s in ctx.snapshot.get("service_connections", []):
        consumers = sorted({u["consumer"] for u in usage if u["resource"].lower() == s["name"].lower()})
        out.append({
            "Service Connection": s["name"], "Type": s.get("type_label"), "Auth Scheme": s.get("auth_scheme"),
            "Ready": s.get("is_ready"), "Shared": s.get("is_shared"), "Shared Projects": s.get("shared_projects"),
            "Open Access": s.get("open_access"), "Authorised Pipelines": len(s.get("authorized_pipelines", [])),
            "Checks": joined(c["type"] for c in s.get("checks", [])),
            "Subscription": (s.get("data") or {}).get("subscriptionName"),
            "Scope Level": (s.get("data") or {}).get("scopeLevel"),
            "Creation Mode": (s.get("data") or {}).get("creationMode"),
            "Created By": s.get("created_by"), "Last Used": dt(s.get("last_used")),
            "Days Since Use": _days(s.get("last_used"), ctx), "Executions (sampled)": s.get("execution_count"),
            "Consumers (count)": len(consumers), "Consumers": joined(consumers),
            "Unused": not consumers and not s.get("execution_count"), "Expiry": s.get("expiry"),
            "URL": s.get("web_url"),
        })
    return out


def service_connection_usage(ctx: RunContext) -> Rows:
    return [{"Service Connection": u["resource"], "Consumer": u["consumer"], "Consumer Type": u["consumer_type"],
             "Evidence": u["source"], "Known Connection": u.get("known")}
            for u in ctx.analysis.get("service_connection_usage", [])]


def environments(ctx: RunContext) -> Rows:
    usage = ctx.analysis.get("environment_usage", [])
    out = []
    for e in ctx.snapshot.get("environments", []):
        deps = e.get("deployments", [])
        last = max((d.get("start_time") for d in deps if d.get("start_time")), default=None)
        checks = [c for c in e.get("checks", []) if not c.get("disabled")]
        out.append({
            "Environment": e["name"], "Production": ctx.settings.is_production(e["name"]),
            "Resources": len(e.get("resources", [])),
            "Kubernetes": joined(f"{r['name']} ({r.get('namespace')})" for r in e.get("resources", [])
                                 if r["type"] == "kubernetes"),
            "Virtual Machines": joined(r["name"] for r in e.get("resources", []) if r["type"] == "virtualMachine"),
            "Checks": joined(c["type"] for c in checks), "Approvers": joined(a for c in checks for a in c["approvers"]),
            "Open Access": e.get("open_access"), "Deployments (sampled)": len(deps),
            "Successful": sum(1 for d in deps if d.get("result") == "succeeded"),
            "Last Deployment": dt(last),
            "Pipelines": joined(sorted({u["consumer"] for u in usage if u["resource"].lower() == e["name"].lower()})),
            "Created By": e.get("created_by"), "Created": dt(e.get("created_on")),
        })
    return out


def release_pipelines(ctx: RunContext) -> Rows:
    usage = ctx.analysis.get("service_connection_usage", [])
    out = []
    for r in ctx.snapshot.get("release_pipelines", []):
        for s in r.get("stages", []) or [{}]:
            if not s:
                continue
            out.append({
                "Release Pipeline": r["name"], "Folder": r.get("folder"), "Stage": s["name"], "Rank": s.get("rank"),
                "Production": ctx.settings.is_production(s["name"]),
                "Depends On": joined(s.get("depends_on")),
                "Pre-deploy Approval": not s["pre_approvals"]["automated"],
                "Approvers": joined(s["pre_approvals"]["approvers"]),
                "Pre-deploy Gates": joined(s["pre_gates"]["gates"]) if s["pre_gates"]["enabled"] else "",
                "Post-deploy Approval": not s["post_approvals"]["automated"],
                "Phases": joined(f"{p['name']} ({p['type']})" for p in s.get("phases", [])),
                "Tasks": len(s.get("steps", [])),
                "Task Names": joined(dict.fromkeys(t["task"] for t in s.get("steps", []))),
                "Service Connections": joined(sorted({u["resource"] for u in usage if u.get("release") == r["name"]
                                                      and u.get("stage") == s["name"]})),
                "Variables": len(s.get("variables", [])),
                "Artifacts": joined(f"{a['type']}:{a.get('source')}" for a in r.get("artifacts", [])),
                "Modified": dt(r.get("modified_on")), "Modified By": r.get("modified_by"), "URL": r.get("web_url"),
            })
    return out


def agent_pools(ctx: RunContext) -> Rows:
    usage = ctx.analysis.get("agent_pool_usage", [])
    builds = ctx.analysis.get("agent_pool_builds", {})
    out = []
    for p in ctx.snapshot.get("agent_pools", []):
        agents = p.get("agents", [])
        consumers = sorted({u["consumer"] for u in usage if u["resource"] == p["name"]})
        out.append({
            "Agent Pool": p["name"], "Hosting": "Microsoft-hosted" if p["is_hosted"] else "Self-hosted",
            "Elastic (VMSS)": p.get("is_elastic"), "Agents": len(agents), "Online": p.get("online_agents"),
            "Offline": sum(1 for a in agents if a["status"] != "online"),
            "Agent Versions": joined(sorted({a["version"] for a in agents if a.get("version")})),
            "Operating Systems": joined(sorted({a["os"] for a in agents if a.get("os")})),
            "Auto Update": p.get("auto_update"), "Open Access": p.get("open_access"),
            "Checks": joined(c["type"] for c in p.get("checks", [])),
            "Runs (window)": builds.get(p["name"], 0),
            "Consumers (count)": len(consumers), "Consumers": joined(consumers),
            "Demands Used": joined(sorted({u.get("demands") for u in usage if u["resource"] == p["name"] and u.get("demands")})),
        })
    return out


def agents(ctx: RunContext) -> Rows:
    return [{"Agent Pool": p["name"], "Agent": a["name"], "Version": a.get("version"), "Status": a.get("status"),
             "Enabled": a.get("enabled"), "OS": a.get("os"),
             "User Capabilities": joined(f"{k}={v}" for k, v in (a.get("user_capabilities") or {}).items()),
             "System Capabilities": a.get("system_capability_count")}
            for p in ctx.snapshot.get("agent_pools", []) for a in p.get("agents", [])]


def work_items(ctx: RunContext) -> Rows:
    out = []
    for w in (ctx.snapshot.get("boards") or {}).get("work_items", []):
        out.append({
            "ID": w["id"], "Type": w.get("type"), "Title": w.get("title"), "State": w.get("state"),
            "State Category": w.get("state_category"), "Assigned To": w.get("assigned_to"),
            "Created": dt(w.get("created")), "Activated": dt(w.get("activated")), "Closed": dt(w.get("closed")),
            "Lead Time (days)": round(d, 1) if (d := days_between(w.get("created"), w.get("closed"))) is not None else None,
            "Cycle Time (days)": round(d, 1) if (d := days_between(w.get("activated"), w.get("closed"))) is not None
            else None,
            "Area Path": w.get("area"), "Iteration Path": w.get("iteration"), "Parent": w.get("parent"),
            "Priority": w.get("priority"), "Story Points": w.get("story_points"), "Tags": joined(w.get("tags")),
        })
    return out


def wiki(ctx: RunContext) -> Rows:
    analysis = {w["wiki"]: w for w in ctx.analysis.get("wiki", [])}
    out = []
    for wk in (ctx.snapshot.get("wiki") or {}).get("wikis", []):
        info = analysis.get(wk["name"], {})
        for page in wk.get("pages", []):
            out.append({
                "Wiki": wk["name"], "Wiki Type": wk.get("type"), "Page": page["path"], "Depth": page.get("depth"),
                "Has Children": page.get("is_parent"), "Views (30d)": page.get("view_count_30d"),
                "Last Author": page.get("last_author"), "Last Modified": dt(page.get("last_modified")),
                "Inbound Links": (info.get("inbound") or {}).get(page["path"], 0),
                "Outbound Links": len(page.get("links", [])),
                "Broken Links": joined((info.get("broken_links") or {}).get(page["path"], [])),
                "Orphaned": page["path"] in (info.get("orphans") or []),
            })
    return out


def _finding_rows(findings) -> Rows:
    return [{
        "Finding ID": f.id, "Rule": f.rule_id, "Severity": f.severity.value, "Category": f.category,
        "Resource Type": f.resource_type, "Resource": f.resource, "Title": f.title, "Description": f.description,
        "Impact": f.impact, "Recommendation": f.recommendation, "Remediation Steps": joined(f.remediation, "\n"),
        "URL": f.url, "Owner": None, "Status": "Open", "Target Date": None,
    } for f in findings]


def security_findings(ctx: RunContext) -> Rows:
    return _finding_rows(f for f in ctx.assessment.sorted_findings() if f.category == "Security")


def risk_register(ctx: RunContext) -> Rows:
    return _finding_rows(ctx.assessment.sorted_findings())


def metrics(ctx: RunContext) -> Rows:
    out = []
    m = ctx.analysis.get("metrics") or {}
    for section in ("activity", "boards", "tests", "cost", "inventory"):
        for key, value in (m.get(section) or {}).items():
            if isinstance(value, dict):
                if key in ("lead_time_days", "cycle_time_days"):
                    for k2, v2 in value.items():
                        out.append({"Section": section, "Metric": f"{key}.{k2}", "Value": v2})
                continue
            out.append({"Section": section, "Metric": key, "Value": value})
    for wtype, info in ((m.get("boards") or {}).get("per_type") or {}).items():
        out.append({"Section": "boards.per_type", "Metric": f"{wtype}.total", "Value": info["total"]})
        out.append({"Section": "boards.per_type", "Metric": f"{wtype}.open", "Value": info["open"]})
        out.append({"Section": "boards.per_type", "Metric": f"{wtype}.lead_time_median",
                    "Value": info["lead_time_days"]["median"]})
        out.append({"Section": "boards.per_type", "Metric": f"{wtype}.cycle_time_median",
                    "Value": info["cycle_time_days"]["median"]})
    for key, value in (ctx.analysis.get("technical_debt") or {}).get("components", {}).items():
        out.append({"Section": "technical_debt", "Metric": key, "Value": value})
    for key, value in (ctx.analysis.get("risk") or {}).get("by_category", {}).items():
        out.append({"Section": "risk_by_category", "Metric": key, "Value": value})
    return out


def recommendations(ctx: RunContext) -> Rows:
    return [{"Priority": i + 1, "Severity": r["severity"], "Title": r["title"], "Category": r["category"],
             "Occurrences": r["occurrences"], "Resources": joined(r["resources"][:30]), "Impact": r["impact"],
             "Recommendation": r["recommendation"], "Remediation Steps": joined(r["remediation"], "\n")}
            for i, r in enumerate(ctx.analysis.get("recommendations", []))]


def anti_patterns(ctx: RunContext) -> Rows:
    return [{"Pipeline": a["pipeline"], "Rule": a["rule"], "Severity": a["severity"], "Title": a["title"],
             "Detail": a["detail"], "Recommendation": a["recommendation"]} for a in ctx.analysis.get("anti_patterns", [])]


def permissions(ctx: RunContext) -> Rows:
    return [{"Namespace": e["namespace"], "Resource": e["resource"], "Identity": e.get("identity"),
             "Identity Kind": e.get("identity_kind"), "Allow": joined(e["allow"]), "Deny": joined(e["deny"])}
            for e in (ctx.snapshot.get("security") or {}).get("acl_entries", [])]


def group_members(ctx: RunContext) -> Rows:
    return [{"Group": g["display_name"], "Custom Group": g.get("is_custom"), "Member": m["name"],
             "Member Kind": m["kind"], "Principal": m.get("principal"), "Origin": m.get("origin")}
            for g in (ctx.snapshot.get("security") or {}).get("groups", []) for m in g["members"] or [
                {"name": "(empty)", "kind": "", "principal": None, "origin": None}]]


def test_plans(ctx: RunContext) -> Rows:
    return [{"Test Plan": p["name"], "State": p.get("state"), "Suite": s["name"], "Suite Type": s.get("type"),
             "Test Cases": s.get("test_case_count"), "Automated": s.get("automated_count"),
             "Automation %": round(s["automated_count"] / s["test_case_count"], 3) if s.get("test_case_count") else None,
             "Owner": p.get("owner"), "Area": p.get("area"), "Iteration": p.get("iteration")}
            for p in (ctx.snapshot.get("test_plans") or {}).get("plans", []) for s in p.get("suites", [])]


def feeds(ctx: RunContext) -> Rows:
    consumers = ctx.analysis.get("feed_consumers", {})
    return [{"Feed": f["name"], "Scope": f.get("scope"), "Package": p["name"], "Protocol": p.get("protocol"),
             "Latest Version": p.get("version"), "Published": dt(p.get("publish_date")), "Age (days)": p.get("age_days"),
             "Upstreams": joined(u["name"] for u in f.get("upstream_sources", [])),
             "Consumers": joined(consumers.get(f["name"], []))}
            for f in ctx.snapshot.get("feeds", []) for p in f.get("packages", []) or [
                {"name": "(no packages)", "protocol": None, "version": None, "publish_date": None, "age_days": None}]]


def secure_files(ctx: RunContext) -> Rows:
    return [{"Secure File": f["name"], "Open Access": f.get("open_access"),
             "Authorised Pipelines": len(f.get("authorized_pipelines", [])),
             "Checks": joined(c["type"] for c in f.get("checks", [])),
             "Created By": f.get("created_by"), "Modified": dt(f.get("modified_on"))}
            for f in ctx.snapshot.get("secure_files", [])]


def coverage(ctx: RunContext) -> Rows:
    return [{"Plugin": c.collector, "Area": c.area, "Status": c.status, "Message": c.message[:500]}
            for c in ctx.assessment.coverage if c.status != "ok"]


# Ordered sheet registry: (sheet name, builder, description)
SHEETS: list[tuple[str, Callable[[RunContext], Rows], str]] = [
    ("Summary", summary, "Project metadata, scores and inventory"),
    ("Repositories", repositories, "Repository inventory, activity and branch protection"),
    ("Pipelines", pipelines, "Build & YAML pipeline inventory"),
    ("PipelineDependencies", pipeline_dependencies, "Triggers, resources and artifact flows"),
    ("Templates", templates, "YAML template usage"),
    ("Variables", variables, "Variables and secrets by owner"),
    ("VariableGroups", variable_groups, "Variable groups & Key Vault links"),
    ("VariableMatrix", variable_matrix, "Consumer x variable group usage matrix"),
    ("ServiceConnections", service_connections, "Service connections and usage"),
    ("ServiceConnectionUsage", service_connection_usage, "Service connection consumers with evidence"),
    ("Environments", environments, "Environments, resources and checks"),
    ("ReleasePipelines", release_pipelines, "Classic release stages, approvals and gates"),
    ("AgentPools", agent_pools, "Agent pools and usage"),
    ("Agents", agents, "Self-hosted agents and capabilities"),
    ("WorkItems", work_items, "Work items with lead/cycle time"),
    ("Wiki", wiki, "Wiki pages, ownership and link health"),
    ("TestPlans", test_plans, "Test plans, suites and automation"),
    ("Feeds", feeds, "Artifacts feeds and packages"),
    ("SecureFiles", secure_files, "Secure files"),
    ("Permissions", permissions, "Explicit ACL entries"),
    ("GroupMembers", group_members, "Security group membership"),
    ("SecurityFindings", security_findings, "Security-related findings"),
    ("RiskRegister", risk_register, "All findings as a risk register"),
    ("AntiPatterns", anti_patterns, "YAML anti-patterns"),
    ("Metrics", metrics, "Activity, flow, test and cost metrics"),
    ("Recommendations", recommendations, "Prioritised recommendations"),
    ("Coverage", coverage, "Areas not collected (permissions / features)"),
]


def build_all(ctx: RunContext) -> dict[str, Rows]:
    return {name: builder(ctx) for name, builder, _ in SHEETS}
