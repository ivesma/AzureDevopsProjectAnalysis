"""Git repositories: branches, policies, commits, contributors, pull requests and YAML content."""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any

from azdo_analyser.core.base import Collector
from azdo_analyser.core.client import seg
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import days_between, identity_name, parse_date, short_ref

YAML_RE = re.compile(r"\.ya?ml$", re.IGNORECASE)
SKIP_DIRS = ("/node_modules/", "/.git/", "/vendor/", "/bin/", "/obj/", "/.terraform/")

TECH_MARKERS = {
    "package.json": "Node.js", ".csproj": ".NET", ".sln": ".NET", "pom.xml": "Java (Maven)",
    "build.gradle": "Java (Gradle)", "requirements.txt": "Python", "pyproject.toml": "Python",
    "go.mod": "Go", "Cargo.toml": "Rust", "Dockerfile": "Docker", ".tf": "Terraform",
    ".bicep": "Bicep", "Chart.yaml": "Helm", "kustomization.yaml": "Kustomize", ".ps1": "PowerShell",
}

POLICY_TYPES = {
    "Minimum number of reviewers": "min_reviewers",
    "Build": "build_validation",
    "Comment requirements": "comment_resolution",
    "Work item linking": "work_item_linking",
    "Require a merge strategy": "merge_strategy",
    "Required reviewers": "required_reviewers",
    "Status": "status_check",
    "File size restriction": "file_size",
    "Path Length restriction": "path_length",
    "Reserved names restriction": "reserved_names",
    "File name restriction": "file_name",
    "Commit author email validation": "author_email",
}


def normalise_policy(cfg: dict) -> dict:
    settings = cfg.get("settings") or {}
    scopes = settings.get("scope") or [{}]
    return {
        "id": cfg.get("id"),
        "type": (cfg.get("type") or {}).get("displayName", "Unknown"),
        "enabled": bool(cfg.get("isEnabled")),
        "blocking": bool(cfg.get("isBlocking")),
        "deleted": bool(cfg.get("isDeleted")),
        "scopes": [
            {"repository_id": s.get("repositoryId"), "ref": s.get("refName"), "match": s.get("matchKind", "Exact")}
            for s in scopes
        ],
        "settings": {k: v for k, v in settings.items() if k != "scope"},
        "created_by": identity_name(cfg.get("createdBy")),
        "created_date": cfg.get("createdDate"),
    }


def policy_applies(policy: dict, repo_id: str, branch: str | None) -> bool:
    for scope in policy["scopes"]:
        if scope["repository_id"] not in (None, repo_id):
            continue
        ref = scope["ref"]
        if ref is None:
            return True
        if not branch:
            continue
        if scope["match"] == "Prefix" and branch.startswith(ref):
            return True
        if scope["match"] == "DefaultBranch" or ref == branch:
            return True
    return False


def summarise_branch_policies(policies: list[dict]) -> dict:
    active = [p for p in policies if p["enabled"] and not p["deleted"]]
    summary: dict[str, Any] = {
        "count": len(active),
        "blocking_count": sum(1 for p in active if p["blocking"]),
        "min_reviewers": None,
        "creator_vote_counts": None,
        "reset_on_push": None,
        "build_validation": False,
        "build_validation_definitions": [],
        "comment_resolution": False,
        "work_item_linking": False,
        "required_reviewers": False,
        "merge_strategies": [],
        "types": sorted({p["type"] for p in active}),
    }
    for p in active:
        key = POLICY_TYPES.get(p["type"])
        s = p["settings"]
        if key == "min_reviewers":
            summary["min_reviewers"] = max(summary["min_reviewers"] or 0, int(s.get("minimumApproverCount", 0)))
            summary["creator_vote_counts"] = bool(s.get("creatorVoteCounts"))
            summary["reset_on_push"] = bool(s.get("resetOnSourcePush") or s.get("resetRejectionsOnSourcePush"))
        elif key == "build_validation":
            summary["build_validation"] = True
            if s.get("buildDefinitionId"):
                summary["build_validation_definitions"].append(s.get("buildDefinitionId"))
        elif key == "merge_strategy":
            mapping = {"allowSquash": "squash", "allowNoFastForward": "merge (no fast-forward)",
                       "allowRebase": "rebase", "allowRebaseMerge": "rebase + merge"}
            summary["merge_strategies"] = [label for k, label in mapping.items() if s.get(k)]
        elif key in ("comment_resolution", "work_item_linking", "required_reviewers"):
            summary[key] = True
    return summary


def parse_gitmodules(text: str) -> list[dict]:
    modules, current = [], {}
    for line in text.splitlines():
        line = line.strip()
        header = re.match(r'^\[submodule\s+"(.+)"\]$', line)
        if header:
            current = {"name": header.group(1)}
            modules.append(current)
        elif "=" in line and current is not None:
            key, value = (x.strip() for x in line.split("=", 1))
            current[key] = value
    return modules


def detect_technologies(paths: list[str]) -> list[str]:
    found = set()
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        for marker, tech in TECH_MARKERS.items():
            if (marker.startswith(".") and name.endswith(marker)) or name == marker:
                found.add(tech)
    return sorted(found)


@register_collector("repositories", order=20, depends_on=("project",))
class RepositoryCollector(Collector):
    """Git repositories, branch policies, commit activity, contributors and pipeline YAML files."""

    section = "repositories"

    def collect(self, ctx: RunContext) -> list[dict]:
        c, p = ctx.client, ctx.project
        repos = ctx.safe("repositories", lambda: c.get_all(f"{p}/_apis/git/repositories"), [])
        raw_policies = ctx.safe("policies", lambda: c.get_all(f"{p}/_apis/policy/configurations"), [])
        policies = [normalise_policy(x) for x in raw_policies]
        ctx.snapshot["policies"] = policies
        yaml_files: dict[str, str] = ctx.snapshot.setdefault("yaml_files", {})
        window_start = ctx.now - timedelta(days=ctx.settings.history_days)

        results = []
        for repo in sorted(repos, key=lambda r: r.get("name", "").lower()):
            results.append(self._repo(ctx, repo, policies, window_start, yaml_files))
        return results

    def _repo(self, ctx: RunContext, repo: dict, policies: list[dict], window_start, yaml_files) -> dict:
        c, p, s = ctx.client, ctx.project, ctx.settings
        rid, name = repo["id"], repo.get("name", "")
        base = f"{p}/_apis/git/repositories/{rid}"
        default_branch = repo.get("defaultBranch")
        disabled = bool(repo.get("isDisabled"))
        record: dict[str, Any] = {
            "id": rid,
            "name": name,
            "default_branch": short_ref(default_branch),
            "size_bytes": repo.get("size", 0),
            "remote_url": repo.get("remoteUrl"),
            "web_url": repo.get("webUrl"),
            "is_disabled": disabled,
            "is_fork": bool(repo.get("isFork")),
            "is_empty": not default_branch,
            "branches": [],
            "branch_count": 0,
            "locked_branches": [],
            "last_commit_date": None,
            "commits_in_window": 0,
            "commits_by_week": {},
            "contributors": [],
            "contributor_count": 0,
            "pull_requests": {},
            "policies": [],
            "default_branch_policies": summarise_branch_policies([]),
            "yaml_files": [],
            "submodules": [],
            "technologies": [],
        }
        applicable = [x for x in policies if policy_applies(x, rid, default_branch)]
        record["policies"] = [x["id"] for x in policies if any(sc["repository_id"] == rid for sc in x["scopes"])]
        record["default_branch_policies"] = summarise_branch_policies(applicable)
        if disabled or not default_branch:
            return record

        refs = ctx.safe(f"refs:{name}", lambda: c.get_all(f"{base}/refs", {"filter": "heads/"}), [])
        record["branches"] = sorted(short_ref(r["name"]) for r in refs)
        record["branch_count"] = len(refs)
        record["locked_branches"] = sorted(short_ref(r["name"]) for r in refs if r.get("isLocked"))

        commits = ctx.safe(
            f"commits:{name}",
            lambda: c.get_skip_paged(
                f"{base}/commits",
                {"searchCriteria.fromDate": window_start.isoformat()},
                page_size=500, limit=s.max_commits_per_repo,
                top_param="searchCriteria.$top", skip_param="searchCriteria.$skip",
            ),
            [],
        )
        if not commits:  # nothing recent - fetch the latest commit to find the last activity date
            commits_latest = ctx.safe(f"latest-commit:{name}",
                                      lambda: c.get(f"{base}/commits", {"searchCriteria.$top": 1}).get("value", []), [])
        else:
            commits_latest = commits[:1]
        if commits_latest:
            record["last_commit_date"] = (commits_latest[0].get("committer") or {}).get("date")
        record["commits_in_window"] = len(commits)
        authors: Counter[str] = Counter()
        names: dict[str, str] = {}
        weeks: Counter[str] = Counter()
        for commit in commits:
            author = commit.get("author") or {}
            key = (author.get("email") or author.get("name") or "unknown").lower()
            authors[key] += 1
            names[key] = author.get("name") or key
            dt = parse_date(author.get("date"))
            if dt:
                iso_year, iso_week, _ = dt.isocalendar()
                weeks[f"{iso_year}-W{iso_week:02d}"] += 1
        record["contributors"] = [{"name": names[k], "email": k, "commits": n} for k, n in authors.most_common()]
        record["contributor_count"] = len(authors)
        record["commits_by_week"] = dict(sorted(weeks.items()))

        prs = ctx.safe(
            f"pull-requests:{name}",
            lambda: c.get_skip_paged(f"{base}/pullrequests", {"searchCriteria.status": "all"},
                                     page_size=100, limit=s.max_pull_requests_per_repo),
            [],
        )
        record["pull_requests"] = self._pr_stats(prs, window_start)

        if s.scan_repository_yaml:
            self._scan_files(ctx, record, base, default_branch, yaml_files)
        return record

    @staticmethod
    def _pr_stats(prs: list[dict], window_start) -> dict:
        status = Counter(pr.get("status", "unknown") for pr in prs)
        completion_hours, reviewers, strategies = [], Counter(), Counter()
        completed_in_window = created_in_window = 0
        for pr in prs:
            created = parse_date(pr.get("creationDate"))
            if created and created >= window_start:
                created_in_window += 1
            if pr.get("status") == "completed":
                hours = days_between(pr.get("creationDate"), pr.get("closedDate"))
                if hours is not None:
                    completion_hours.append(hours * 24)
                closed = parse_date(pr.get("closedDate"))
                if closed and closed >= window_start:
                    completed_in_window += 1
                strategy = (pr.get("completionOptions") or {}).get("mergeStrategy")
                if strategy:
                    strategies[strategy] += 1
            for reviewer in pr.get("reviewers") or []:
                if reviewer.get("vote", 0) != 0:
                    reviewers[identity_name(reviewer) or "unknown"] += 1
        avg = round(sum(completion_hours) / len(completion_hours), 1) if completion_hours else None
        return {
            "total": len(prs),
            "active": status.get("active", 0),
            "completed": status.get("completed", 0),
            "abandoned": status.get("abandoned", 0),
            "created_in_window": created_in_window,
            "completed_in_window": completed_in_window,
            "avg_completion_hours": avg,
            "merge_strategies_used": dict(strategies),
            "top_reviewers": dict(reviewers.most_common(10)),
        }

    @staticmethod
    def _scan_files(ctx: RunContext, record: dict, base: str, branch: str, yaml_files: dict) -> None:
        c, s, name = ctx.client, ctx.settings, record["name"]
        version = {"versionDescriptor.version": short_ref(branch), "versionDescriptor.versionType": "branch"}
        items = ctx.safe(
            f"tree:{name}",
            lambda: c.get(f"{base}/items", {"scopePath": "/", "recursionLevel": "Full", **version}).get("value", []),
            [],
        )
        paths = [i["path"] for i in items if not i.get("isFolder") and i.get("gitObjectType", "blob") == "blob"]
        record["file_count"] = len(paths)
        record["technologies"] = detect_technologies(paths)
        yaml_paths = [x for x in paths if YAML_RE.search(x) and not any(d in x + "/" for d in SKIP_DIRS)]
        record["yaml_files"] = yaml_paths[: s.max_yaml_files_per_repo]
        for path in record["yaml_files"]:
            content = ctx.safe(
                f"yaml:{name}:{path}",
                lambda pth=path: c.get(f"{base}/items", {"path": pth, "includeContent": "true", **version}).get("content"),
                None,
            )
            if content is not None:
                yaml_files[f"{name}:{path}"] = content
        if "/.gitmodules" in paths:
            text = ctx.safe(f"gitmodules:{name}", lambda: c.get(
                f"{base}/items", {"path": "/.gitmodules", "includeContent": "true", **version}).get("content"), None)
            record["submodules"] = parse_gitmodules(text or "")


def fetch_file(ctx: RunContext, repo_id: str, path: str, branch: str | None = None) -> str | None:
    """Fetch a single file's content from an Azure Repos Git repository."""
    params = {"path": path, "includeContent": "true"}
    if branch:
        params.update({"versionDescriptor.version": short_ref(branch), "versionDescriptor.versionType": "branch"})
    return ctx.safe(
        f"file:{repo_id}:{path}",
        lambda: ctx.client.get(f"{ctx.project}/_apis/git/repositories/{seg(repo_id)}/items", params).get("content"),
        None,
    )
