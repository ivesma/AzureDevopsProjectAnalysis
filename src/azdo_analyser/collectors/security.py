"""Security: groups & memberships, service identities, ACLs, user access and PAT audit events."""

from __future__ import annotations

from datetime import timedelta

from azdo_analyser.core.base import Collector
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.plugin import register_collector
from azdo_analyser.core.utils import chunks, identity_name

PREVIEW = "7.1-preview.1"

NAMESPACES = {
    "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87": ("Git Repositories", "repoV2/{pid}"),
    "33344d9c-fc72-4d6f-aba5-fa317101a7e9": ("Build", "{pid}"),
    "c788c23e-1b46-4162-8f5e-d7585343b5de": ("ReleaseManagement", "{pid}"),
    "52d39943-cb85-4d7f-8fa8-c6baac873819": ("Project", "$PROJECT:vstfs:///Classification/TeamProject/{pid}"),
}

AUDIT_PREFIXES = ("Token.", "Policy.", "Security.", "Group.", "Pipelines.PipelineModified", "Library.")


def subject_kind(subject: dict) -> str:
    descriptor = subject.get("descriptor") or ""
    kind = subject.get("subjectKind") or ""
    if descriptor.startswith("svc.") or "Build Service" in (subject.get("displayName") or ""):
        return "build_identity"
    if descriptor.startswith("aadsp.") or kind == "servicePrincipal":
        return "service_principal"
    if kind == "group" or descriptor.split(".")[0] in ("vssgp", "aadgp"):
        return "group"
    return "user"


def decode_bits(mask: int, actions: list[dict]) -> list[str]:
    return [a["name"] for a in actions if mask & int(a.get("bit", 0))]


@register_collector("security", order=130, depends_on=("project", "repositories", "pipelines", "releases"))
class SecurityCollector(Collector):
    """Project security groups, members, service accounts, ACLs, user access levels and PAT audit events."""

    section = "security"

    def collect(self, ctx: RunContext) -> dict:
        project = ctx.snapshot.get("project") or {}
        pid = project.get("id")
        groups = self._groups(ctx, pid)
        members = {m["descriptor"]: m for g in groups for m in g["members"]}
        admins = next((g for g in groups if g["name"].endswith("Project Administrators")), None)
        result = {
            "groups": groups,
            "project_admins": [m["name"] for m in (admins or {}).get("members", [])],
            "project_admin_users": [m["name"] for m in (admins or {}).get("members", []) if m["kind"] == "user"],
            "service_accounts": [m for m in members.values() if m["kind"] in ("service_principal", "build_identity")],
            "build_identities": [m["name"] for m in members.values() if m["kind"] == "build_identity"],
            "acl_entries": self._acls(ctx, pid) if pid else [],
            "users": self._users(ctx, members),
            "audit_events": self._audit(ctx),
        }
        result["pat_events"] = [e for e in result["audit_events"] if e["action"].startswith("Token.")]
        return result

    # ----------------------------------------------------------- groups
    def _groups(self, ctx: RunContext, pid: str | None) -> list[dict]:
        c = ctx.client
        if not pid:
            return []
        descriptor = ctx.safe("project-descriptor",
                              lambda: c.get(f"_apis/graph/descriptors/{pid}", service="vssps",
                                            api_version=PREVIEW).get("value"), None)
        if not descriptor:
            return []
        raw_groups = ctx.safe("groups", lambda: c.get_all("_apis/graph/groups", {"scopeDescriptor": descriptor},
                                                          service="vssps", api_version=PREVIEW), [])
        memberships = {}
        for g in raw_groups:
            memberships[g["descriptor"]] = ctx.safe(
                f"memberships:{g.get('displayName')}",
                lambda gg=g: c.get_all(f"_apis/graph/Memberships/{gg['descriptor']}", {"direction": "down"},
                                       service="vssps", api_version=PREVIEW),
                [],
            )
        descriptors = sorted({m["memberDescriptor"] for ms in memberships.values() for m in ms})
        subjects: dict[str, dict] = {}
        for batch in chunks(descriptors, 100):
            data = ctx.safe("subject-lookup", lambda b=batch: c.post(
                "_apis/graph/subjectlookup", {"lookupKeys": [{"descriptor": d} for d in b]},
                service="vssps", api_version=PREVIEW), {})
            subjects.update(data.get("value") or {})
        groups = []
        for g in raw_groups:
            members = []
            for m in memberships.get(g["descriptor"], []):
                subject = subjects.get(m["memberDescriptor"], {"descriptor": m["memberDescriptor"]})
                members.append({
                    "descriptor": m["memberDescriptor"],
                    "name": subject.get("displayName") or subject.get("principalName") or m["memberDescriptor"],
                    "principal": subject.get("principalName") or subject.get("mailAddress"),
                    "kind": subject_kind(subject),
                    "origin": subject.get("origin"),
                })
            groups.append({
                "descriptor": g["descriptor"],
                "name": g.get("principalName") or g.get("displayName"),
                "display_name": g.get("displayName"),
                "description": g.get("description") or "",
                "origin": g.get("origin"),
                "is_custom": g.get("displayName") not in DEFAULT_GROUPS,
                "members": members,
                "member_count": len(members),
            })
        return groups

    # ------------------------------------------------------------- ACLs
    def _acls(self, ctx: RunContext, pid: str) -> list[dict]:
        c = ctx.client
        namespaces = {n["namespaceId"]: n for n in ctx.safe("security-namespaces",
                                                             lambda: c.get_all("_apis/securitynamespaces"), [])}
        names = self._resource_names(ctx, pid)
        raw_entries = []
        for ns_id, (label, token_tpl) in NAMESPACES.items():
            actions = (namespaces.get(ns_id) or {}).get("actions") or []
            acls = ctx.safe(f"acl:{label}", lambda n=ns_id, t=token_tpl: c.get_all(
                f"_apis/accesscontrollists/{n}", {"token": t.format(pid=pid), "recurse": "true",
                                                  "includeExtendedInfo": "false"}), [])
            for acl in acls:
                for ace in (acl.get("acesDictionary") or {}).values():
                    allow = decode_bits(int(ace.get("allow", 0)), actions)
                    deny = decode_bits(int(ace.get("deny", 0)), actions)
                    if allow or deny:
                        raw_entries.append({
                            "namespace": label,
                            "token": acl.get("token"),
                            "resource": names.get(acl.get("token"), acl.get("token")),
                            "descriptor": ace.get("descriptor"),
                            "allow": allow,
                            "deny": deny,
                        })
        identities = self._identities(ctx, sorted({e["descriptor"] for e in raw_entries}))
        for entry in raw_entries:
            ident = identities.get(entry["descriptor"], {})
            entry["identity"] = ident.get("name") or entry["descriptor"]
            entry["identity_kind"] = ident.get("kind", "unknown")
        return raw_entries

    @staticmethod
    def _resource_names(ctx: RunContext, pid: str) -> dict[str, str]:
        names = {f"repoV2/{pid}": "All repositories", pid: "Project (all)"}
        for repo in ctx.snapshot.get("repositories", []):
            names[f"repoV2/{pid}/{repo['id']}"] = f"repo:{repo['name']}"
        for pipe in ctx.snapshot.get("pipelines", []):
            folder = pipe["folder"].strip("\\").replace("\\", "/")
            token = f"{pid}/{folder}/{pipe['id']}" if folder else f"{pid}/{pipe['id']}"
            names[token] = f"pipeline:{pipe['name']}"
        for rel in ctx.snapshot.get("release_pipelines", []):
            names[f"{pid}/{rel['id']}"] = names.get(f"{pid}/{rel['id']}", f"release:{rel['name']}")
        names[f"$PROJECT:vstfs:///Classification/TeamProject/{pid}"] = "Project"
        return names

    @staticmethod
    def _identities(ctx: RunContext, descriptors: list[str]) -> dict[str, dict]:
        out = {}
        for batch in chunks(descriptors, 40):
            data = ctx.safe("identities", lambda b=batch: ctx.client.get(
                "_apis/identities", {"descriptors": ",".join(b)}, service="vssps"), {})
            for ident in data.get("value", []) if isinstance(data, dict) else []:
                if not ident:
                    continue
                name = ident.get("customDisplayName") or ident.get("providerDisplayName")
                kind = "group" if ident.get("isContainer") else "user"
                if "ServiceIdentity" in (ident.get("descriptor") or "") or "Build Service" in (name or ""):
                    kind = "build_identity"
                out[ident.get("descriptor")] = {"name": name, "kind": kind}
        return out

    # ------------------------------------------------------- user access
    @staticmethod
    def _users(ctx: RunContext, members: dict[str, dict]) -> list[dict]:
        principals = {(m.get("principal") or "").lower() for m in members.values() if m["kind"] == "user"}
        users, token = [], None
        for _ in range(20):
            params = {"$top": 200}
            if token:
                params["continuationToken"] = token
            data = ctx.safe("user-entitlements", lambda q=params: ctx.client.get(
                "_apis/userentitlements", q, service="vsaex", api_version="7.1-preview.3"), {})
            for m in data.get("members") or data.get("items") or []:
                user = m.get("user") or {}
                principal = (user.get("principalName") or user.get("mailAddress") or "").lower()
                if principals and principal not in principals:
                    continue
                users.append({
                    "name": user.get("displayName"),
                    "principal": principal,
                    "access_level": (m.get("accessLevel") or {}).get("licenseDisplayName"),
                    "last_accessed": m.get("lastAccessedDate"),
                    "date_created": m.get("dateCreated"),
                })
            token = data.get("continuationToken")
            if not token:
                break
        return users

    @staticmethod
    def _audit(ctx: RunContext) -> list[dict]:
        start = (ctx.now - timedelta(days=min(ctx.settings.history_days, 90))).isoformat()
        events, token = [], None
        for _ in range(25):
            params = {"startTime": start, "batchSize": 200}
            if token:
                params["continuationToken"] = token
            data = ctx.safe("audit-log", lambda q=params: ctx.client.get(
                "_apis/audit/auditlog", q, service="auditservice", api_version=PREVIEW), {})
            for e in data.get("decoratedAuditLogEntries") or []:
                action = e.get("actionId") or ""
                if action.startswith(AUDIT_PREFIXES):
                    events.append({
                        "timestamp": e.get("timestamp"),
                        "action": action,
                        "actor": e.get("actorDisplayName") or identity_name(e.get("actorUPN")),
                        "area": e.get("area"),
                        "details": e.get("details"),
                        "project": e.get("projectName"),
                    })
            token = data.get("continuationToken")
            if not data.get("hasMore") or not token:
                break
        return events


DEFAULT_GROUPS = {
    "Project Administrators", "Contributors", "Readers", "Build Administrators", "Project Valid Users",
    "Release Administrators", "Endpoint Administrators", "Endpoint Creators",
}
