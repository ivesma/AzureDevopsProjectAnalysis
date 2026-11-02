"""Rule-based risk engine and recommendations.

Each rule is a function ``(ctx) -> Iterable[Finding]`` registered with :func:`rule`. The engine runs
all rules, de-duplicates findings, computes a 0-100 risk score (higher = riskier) and groups findings
into a prioritised recommendation list.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable, Iterable

from azdo_analyser.core.base import Analyser
from azdo_analyser.core.context import RunContext
from azdo_analyser.core.models import Finding, Severity
from azdo_analyser.core.plugin import register_analyser
from azdo_analyser.core.utils import days_between

RuleFn = Callable[[RunContext], Iterable[Finding]]
RULES: dict[str, RuleFn] = {}

BROAD_GROUPS = ("contributors", "readers", "project valid users", "project collection valid users", "everyone")
DANGEROUS_PERMISSIONS = {
    "Git Repositories": {"PolicyExempt", "PullRequestBypassPolicy", "ForcePush", "ManagePermissions", "EditPolicies",
                         "DeleteRepository", "Administer"},
    "Build": {"AdministerBuildPermissions", "DeleteBuildDefinition", "DestroyBuilds", "EditBuildQuality"},
    "ReleaseManagement": {"AdministerReleasePermissions", "DeleteReleaseDefinition", "ManageReleaseApprovers",
                          "DeleteReleases"},
    "Project": {"DELETE", "UPDATE_VISIBILITY", "MANAGE_SYSTEM_PROPERTIES", "BYPASS_RULES", "MANAGE_PROPERTIES"},
}
BUILD_IDENTITY_DANGEROUS = {"ForcePush", "PolicyExempt", "PullRequestBypassPolicy", "ManagePermissions",
                            "EditPolicies", "AdministerBuildPermissions"}
DEPLOY_TASK_HINTS = ("deploy", "azurecli", "azurepowershell", "kubernetes", "kubectl", "helm", "terraform",
                     "azurewebapp", "azurefunction", "sqlazuredacpac", "azureresourcemanagertemplatedeployment",
                     "azurermwebappdeployment", "arm")
RISK_SCALE = 120
AP_IMPACT = {
    "AP001": "Anyone with read access to the repository (and its history) can read the secret.",
    "AP002": "Jobs fail or queue when the image is removed; retired images stop receiving security updates.",
    "AP003": "A new major task version can change behaviour without review.",
    "AP004": "Failures are hidden; broken artefacts can be published or deployed.",
    "AP005": "Any later step or script can reuse the job token to push to the repository.",
    "AP006": "Remote code runs unverified on the agent with access to the job's secrets and token.",
    "AP007": "Changes to the template branch alter every consuming pipeline without review in those pipelines.",
    "AP008": "Every push to any branch consumes agent capacity and may run untrusted code paths.",
    "AP011": "Toolchain changes when Microsoft rolls the label, making builds non-reproducible.",
    "AP012": "Inline scripts are hard to lint, test and reuse.",
    "AP013": "Scripts holding the job token inherit the build identity's permissions.",
    "AP014": "The pipeline cannot run until the YAML is fixed.",
    "AP015": "Deprecated tasks stop receiving fixes and may be removed.",
    "AP016": "Dependencies of this pipeline could not be fully analysed.",
    "AP020": "Large inline variable blocks are duplicated across pipelines and drift.",
    "AP021": "Deeply nested templates are hard to reason about and debug.",
}
CATEGORY_WEIGHTS = {"Security": 1.3, "Deployment": 1.2, "Source Control": 1.0, "Pipelines": 0.8,
                    "Resources": 0.8, "Governance": 0.7, "Boards": 0.4, "Documentation": 0.3, "Testing": 0.4}


def rule(rule_id: str) -> Callable[[RuleFn], RuleFn]:
    def decorator(fn: RuleFn) -> RuleFn:
        RULES[rule_id] = fn
        fn.rule_id = rule_id  # type: ignore[attr-defined]
        return fn

    return decorator


def finding(rule_id: str, severity: Severity, category: str, resource_type: str, resource: str, title: str,
            description: str, impact: str, recommendation: str, remediation: list[str], url: str | None = None,
            **evidence) -> Finding:
    return Finding(rule_id, title, severity, category, resource_type, resource, description, impact, recommendation,
                   remediation, evidence, url)


# --------------------------------------------------------------------------- source control
@rule("SC001")
def missing_branch_policies(ctx: RunContext):
    for repo in ctx.snapshot.get("repositories", []):
        if repo.get("is_disabled") or repo.get("is_empty"):
            continue
        pol = repo["default_branch_policies"]
        if pol["count"] == 0:
            yield finding(
                "SC001", Severity.HIGH, "Source Control", "repository", repo["name"],
                "Default branch has no branch policies",
                f"'{repo['default_branch']}' in {repo['name']} accepts direct pushes with no review or validation.",
                "Unreviewed or broken code can reach the default branch and be deployed.",
                "Protect the default branch with minimum reviewers and build validation policies.",
                ["Project settings > Repositories > {repo} > Policies > Branch policies".replace("{repo}", repo["name"]),
                 "Require a minimum of 2 reviewers and disallow approving own changes",
                 "Add a build validation policy running the CI pipeline",
                 "Require linked work items and comment resolution"],
                repo.get("web_url"), branch=repo["default_branch"])
            continue
        if not pol["min_reviewers"]:
            yield finding("SC002", Severity.MEDIUM, "Source Control", "repository", repo["name"],
                          "No minimum reviewer policy",
                          f"Pull requests into '{repo['default_branch']}' do not require approval.",
                          "Changes can be merged without peer review (four-eyes principle not enforced).",
                          "Add a 'Minimum number of reviewers' policy (2 recommended).",
                          ["Branch policies > Require a minimum number of reviewers", "Set count to 2",
                           "Disable 'Allow requestors to approve their own changes'"], repo.get("web_url"))
        elif pol["min_reviewers"] < 2 and pol.get("creator_vote_counts"):
            yield finding("SC003", Severity.MEDIUM, "Source Control", "repository", repo["name"],
                          "Authors can approve their own pull requests",
                          "Only one reviewer is required and the creator's vote counts.",
                          "A single person can merge changes without independent review.",
                          "Disallow creator votes or require at least 2 reviewers.",
                          ["Branch policies > Minimum number of reviewers",
                           "Untick 'Allow requestors to approve their own changes'"], repo.get("web_url"))
        if not pol["build_validation"]:
            yield finding("SC004", Severity.MEDIUM, "Source Control", "repository", repo["name"],
                          "No build validation on pull requests",
                          f"'{repo['default_branch']}' has no build validation policy.",
                          "Broken builds and failing tests can be merged.",
                          "Add a build validation policy that runs CI for every pull request.",
                          ["Branch policies > Build validation > Add", "Select the CI pipeline; set to Required"],
                          repo.get("web_url"))
        if pol["count"] and pol["blocking_count"] == 0:
            yield finding("SC005", Severity.MEDIUM, "Source Control", "repository", repo["name"],
                          "Branch policies are optional only",
                          "All configured policies are non-blocking.", "Policies can be ignored on completion.",
                          "Mark key policies as Required.", ["Edit each policy and set it to Required"],
                          repo.get("web_url"))


@rule("SC010")
def stale_repositories(ctx: RunContext):
    for repo in ctx.snapshot.get("repositories", []):
        if repo.get("is_disabled"):
            continue
        if repo.get("is_empty"):
            yield finding("SC011", Severity.LOW, "Governance", "repository", repo["name"], "Empty repository",
                          "The repository has no commits.", "Clutter and confusion for contributors.",
                          "Remove or initialise the repository.", ["Delete the repository if unused"], repo.get("web_url"))
            continue
        age = days_between(repo.get("last_commit_date"), ctx.now)
        if age is not None and age > ctx.settings.stale_days:
            yield finding("SC010", Severity.LOW, "Governance", "repository", repo["name"], "Stale repository",
                          f"No commits for {int(age)} days.", "Unmaintained code accumulates vulnerabilities.",
                          "Archive (disable) the repository or assign an owner.",
                          ["Confirm with owners whether the repo is still needed",
                           "Disable the repository to make it read-only, or delete it"],
                          repo.get("web_url"), last_commit=repo.get("last_commit_date"))


# ------------------------------------------------------------------------------- deployment
@rule("DP001")
def production_environment_without_checks(ctx: RunContext):
    for env in ctx.snapshot.get("environments", []):
        if not ctx.settings.is_production(env["name"]):
            continue
        active = [c for c in env.get("checks", []) if not c.get("disabled")]
        approvals = [c for c in active if c["type"] == "Approval"]
        if not active:
            yield finding("DP001", Severity.HIGH, "Deployment", "environment", env["name"],
                          "Production environment has no approvals or checks",
                          f"Any authorised pipeline can deploy to '{env['name']}' without approval.",
                          "Unreviewed or untested changes can be released directly to production.",
                          "Add an Approval check plus branch control on the production environment.",
                          ["Pipelines > Environments > {0} > Approvals and checks".format(env["name"]),
                           "Add 'Approvals' with a release-manager group (min 1, requester cannot approve)",
                           "Add 'Branch control' limited to refs/heads/main and release/*",
                           "Optionally add Business hours / Azure Monitor checks"])
        elif not approvals:
            yield finding("DP002", Severity.MEDIUM, "Deployment", "environment", env["name"],
                          "Production environment has checks but no human approval",
                          f"Checks: {', '.join(c['type'] for c in active)}.",
                          "Deployments proceed automatically when checks pass.",
                          "Add a manual Approval check for production unless continuous deployment is intended.",
                          ["Environments > Approvals and checks > Approvals"])
        for c in approvals:
            if c.get("requester_can_approve"):
                yield finding("DP003", Severity.MEDIUM, "Deployment", "environment", env["name"],
                              "Requester can approve their own production deployment",
                              "The approval check allows the person who queued the run to approve it.",
                              "Segregation of duties is not enforced.",
                              "Enable 'Approvers cannot approve their own runs'.",
                              ["Edit the Approval check > Advanced > disallow requester approval"])
        if env.get("open_access"):
            yield finding("DP004", Severity.MEDIUM, "Deployment", "environment", env["name"],
                          "Production environment is open to all pipelines",
                          "Pipeline permissions allow every pipeline in the project to target it.",
                          "Any pipeline (including experimental ones) can deploy to production.",
                          "Restrict pipeline permissions to the approved deployment pipelines.",
                          ["Environment > Security > Pipeline permissions > restrict"])


@rule("DP010")
def release_production_approvals(ctx: RunContext):
    for rel in ctx.snapshot.get("release_pipelines", []):
        for stage in rel.get("stages", []):
            if not ctx.settings.is_production(stage["name"]):
                continue
            if stage["pre_approvals"]["automated"]:
                yield finding("DP010", Severity.HIGH, "Deployment", "release stage", f"{rel['name']} / {stage['name']}",
                              "Production deployment missing approval gate",
                              "Pre-deployment approvals are automated (disabled) for this production stage.",
                              "Releases are deployed to production with no human sign-off.",
                              "Enable pre-deployment approvals for the production stage.",
                              ["Edit release pipeline > {0} > Pre-deployment conditions".format(stage["name"]),
                               "Enable Pre-deployment approvals with a release approver group",
                               "Disallow the release creator from approving",
                               "Consider enabling gates (Azure Monitor, work item query)"], rel.get("web_url"))
            elif stage["pre_approvals"].get("release_creator_can_approve"):
                yield finding("DP011", Severity.MEDIUM, "Deployment", "release stage", f"{rel['name']} / {stage['name']}",
                              "Release creator can approve production deployment",
                              "The approval policy allows the release creator to approve.",
                              "Segregation of duties is not enforced.", "Disable 'release creator can approve'.",
                              ["Pre-deployment conditions > Approval policies"], rel.get("web_url"))
            if not stage["pre_gates"]["enabled"]:
                yield finding("DP012", Severity.LOW, "Deployment", "release stage", f"{rel['name']} / {stage['name']}",
                              "No deployment gates on production stage",
                              "Pre-deployment gates are disabled.",
                              "Health signals are not evaluated before deploying.",
                              "Add gates such as Azure Monitor alerts or query work items.",
                              ["Pre-deployment conditions > Gates > Enable"], rel.get("web_url"))


@rule("DP020")
def direct_production_deployment(ctx: RunContext):
    """YAML deployments to production from regular jobs (bypassing environment checks)."""
    effective = ctx.analysis.get("yaml", {}).get("pipelines", {})
    for p in ctx.snapshot.get("pipelines", []):
        model = effective.get(str(p["id"]))
        if not model:
            continue
        for stage in model.get("stages", []):
            for job in stage.get("jobs", []):
                prod = ctx.settings.is_production(stage["name"]) or ctx.settings.is_production(job["name"])
                if prod and job.get("kind") == "job" and model.get("service_connections"):
                    tasks = " ".join(t["task"].lower() for t in model.get("tasks", []))
                    if any(h in tasks for h in DEPLOY_TASK_HINTS):
                        yield finding(
                            "DP020", Severity.HIGH, "Deployment", "pipeline", p["name"],
                            "Direct deployment to Production",
                            f"Stage '{stage['name']}' / job '{job['name']}' deploys using a service connection "
                            "from a regular job instead of a deployment job targeting an environment.",
                            "Environment approvals, checks and deployment history are bypassed.",
                            "Convert the job into a deployment job targeting a protected production environment.",
                            ["Replace 'job:' with 'deployment:' and set 'environment: <prod-env>'",
                             "Configure approvals & checks on the environment",
                             "Add approvals/checks on the production service connection as defence in depth"],
                            p.get("web_url"), stage=stage["name"], job=job["name"])
                        break


# -------------------------------------------------------------------------------- resources
@rule("RS001")
def service_connection_hygiene(ctx: RunContext):
    usage = ctx.analysis.get("service_connection_usage", [])
    used = {r["resource"].lower() for r in usage}
    for sc in ctx.snapshot.get("service_connections", []):
        name = sc["name"]
        last = days_between(sc.get("last_used"), ctx.now) if sc.get("last_used") else None
        if name.lower() not in used and not sc.get("execution_count"):
            yield finding("RS001", Severity.MEDIUM, "Resources", "service connection", name,
                          "Unused service connection",
                          "No pipeline references or execution history were found for this connection.",
                          "Dormant credentials expand the attack surface and are rarely rotated.",
                          "Delete the connection or document its owner and purpose.",
                          ["Confirm with the owner", "Project settings > Service connections > Delete",
                           "Remove the backing service principal / credential"], sc.get("web_url"))
        elif last is not None and last > ctx.settings.stale_days:
            yield finding("RS002", Severity.LOW, "Resources", "service connection", name,
                          "Service connection not used recently", f"Last used {int(last)} days ago.",
                          "Stale credentials may still be valid.", "Review whether it is still required.",
                          ["Review usage history", "Delete or disable if obsolete"], sc.get("web_url"))
        if sc.get("open_access"):
            sev = Severity.HIGH if ctx.settings.is_production(name) else Severity.MEDIUM
            yield finding("RS003", sev, "Security", "service connection", name,
                          "Service connection granted to all pipelines",
                          "'Grant access permission to all pipelines' is enabled.",
                          "Any pipeline - including ones created by any contributor - can use these credentials.",
                          "Restrict pipeline permissions to explicitly authorised pipelines.",
                          ["Service connection > Security > Pipeline permissions", "Disable open access",
                           "Authorise only the pipelines that need it", "Add approvals/checks for production"],
                          sc.get("web_url"))
        if (sc.get("type") or "").lower() == "azurerm" and sc.get("auth_scheme") == "ServicePrincipal":
            yield finding("RS004", Severity.MEDIUM, "Security", "service connection", name,
                          "Secret-based service principal",
                          "The Azure RM connection uses a client secret. Secret expiry is not visible via Azure DevOps.",
                          "Secrets expire (breaking deployments) or leak; rotation is manual.",
                          "Convert to Workload Identity Federation (OIDC) to remove stored secrets.",
                          ["Edit the service connection > Convert to workload identity federation",
                           "Delete the old client secret from the Entra ID app registration"], sc.get("web_url"))
        if sc.get("expiry"):
            remaining = days_between(ctx.now, sc["expiry"])
            if remaining is not None and remaining < 30:
                yield finding("RS005", Severity.HIGH if remaining < 0 else Severity.MEDIUM, "Resources",
                              "service connection", name,
                              "Service connection credential expired" if remaining < 0 else "Credential expiring soon",
                              f"Credential expiry: {sc['expiry']}.", "Deployments will fail.",
                              "Rotate the credential or move to workload identity federation.",
                              ["Renew the secret / certificate", "Update the service connection"], sc.get("web_url"))
        if sc.get("is_ready") is False:
            yield finding("RS006", Severity.MEDIUM, "Resources", "service connection", name,
                          "Service connection is not ready", f"Operation status: {sc.get('operation_status')}.",
                          "Pipelines using it fail.", "Repair or delete the connection.",
                          ["Open the connection and verify / re-authorise"], sc.get("web_url"))
        if ctx.settings.is_production(name) and not sc.get("checks"):
            yield finding("RS007", Severity.MEDIUM, "Deployment", "service connection", name,
                          "Production service connection has no approvals/checks",
                          "Pipelines can use production credentials without approval.",
                          "Bypass of environment protection via regular jobs.",
                          "Add Approval and Branch control checks to the service connection.",
                          ["Service connection > Approvals and checks"], sc.get("web_url"))


@rule("RS010")
def variable_group_hygiene(ctx: RunContext):
    usage = {r["resource"].lower() for r in ctx.analysis.get("variable_group_usage", [])}
    for vg in ctx.snapshot.get("variable_groups", []):
        if vg["name"].lower() not in usage:
            yield finding("RS010", Severity.LOW, "Resources", "variable group", vg["name"], "Unused variable group",
                          "No pipeline or release references this variable group.", "Clutter; stale secrets.",
                          "Delete the group or document its purpose.", ["Pipelines > Library > delete"])
        if vg.get("secret_count") and vg.get("open_access"):
            yield finding("RS011", Severity.MEDIUM, "Security", "variable group", vg["name"],
                          "Secret variable group open to all pipelines",
                          f"{vg['secret_count']} secret(s) are available to every pipeline.",
                          "Any pipeline can read (and exfiltrate) these secrets.",
                          "Restrict pipeline permissions on the variable group.",
                          ["Library > {0} > Pipeline permissions > restrict".format(vg["name"])])
        if vg.get("secret_count") and not vg.get("key_vault"):
            yield finding("RS012", Severity.LOW, "Security", "variable group", vg["name"],
                          "Secrets stored directly in Azure DevOps",
                          f"{vg['secret_count']} secret(s) are not sourced from Azure Key Vault.",
                          "No central rotation, auditing or expiry for these secrets.",
                          "Link the variable group to Azure Key Vault.",
                          ["Create/choose a Key Vault", "Library > group > 'Link secrets from an Azure key vault'"])
    for sf in ctx.snapshot.get("secure_files", []):
        if sf.get("open_access"):
            yield finding("RS013", Severity.MEDIUM, "Security", "secure file", sf["name"],
                          "Secure file open to all pipelines",
                          "Every pipeline can download this file (certificates, keys, profiles).",
                          "Credential material can be exfiltrated by any pipeline.",
                          "Restrict pipeline permissions on the secure file.", ["Library > Secure files > Security"])


@rule("RS020")
def agent_pool_hygiene(ctx: RunContext):
    for pool in ctx.snapshot.get("agent_pools", []):
        if pool["is_hosted"]:
            continue
        agents = pool.get("agents", [])
        if not agents and not pool.get("is_elastic"):
            yield finding("RS020", Severity.MEDIUM, "Resources", "agent pool", pool["name"], "Self-hosted pool has no agents",
                          "Jobs targeting this pool will queue indefinitely.", "Pipeline failures/timeouts.",
                          "Add agents or remove the pool.", ["Organization settings > Agent pools"])
            continue
        offline = [a["name"] for a in agents if a["status"] != "online"]
        if offline:
            yield finding("RS021", Severity.LOW, "Resources", "agent pool", pool["name"], "Offline agents",
                          f"{len(offline)} of {len(agents)} agents are offline: {', '.join(offline[:10])}.",
                          "Reduced capacity; stale machines may be unpatched.", "Repair or remove offline agents.",
                          ["Check agent service on the machines", "Remove decommissioned agents"], agents=offline)
        disabled = [a["name"] for a in agents if not a["enabled"]]
        if disabled:
            yield finding("RS022", Severity.INFO, "Resources", "agent pool", pool["name"], "Disabled agents",
                          f"Disabled: {', '.join(disabled[:10])}.", "Capacity not used.", "Remove if obsolete.", [])
        versions = sorted({a["version"] for a in agents if a.get("version")})
        if len(versions) > 1:
            yield finding("RS023", Severity.LOW, "Resources", "agent pool", pool["name"], "Mixed agent versions",
                          f"Agent versions: {', '.join(versions)}.", "Inconsistent behaviour; outdated agents.",
                          "Enable auto-update and update outdated agents.", ["Agent pool > Settings > auto-update"])
        if pool.get("open_access"):
            yield finding("RS024", Severity.MEDIUM, "Security", "agent pool", pool["name"],
                          "Self-hosted pool open to all pipelines",
                          "Any pipeline can run code on these self-hosted machines.",
                          "Untrusted pipelines (e.g. PR builds) can compromise persistent agents and their network.",
                          "Restrict queue pipeline permissions; use ephemeral agents for PR validation.",
                          ["Project settings > Agent pools > pool > Security > Pipeline permissions"])


@rule("RS030")
def environment_hygiene(ctx: RunContext):
    for env in ctx.snapshot.get("environments", []):
        if not env.get("deployments"):
            yield finding("RS030", Severity.INFO, "Resources", "environment", env["name"], "Environment never deployed to",
                          "No deployment history.", "Clutter.", "Delete unused environments.", [])


# -------------------------------------------------------------------------------- pipelines
@rule("PL001")
def pipeline_health(ctx: RunContext):
    for p in ctx.snapshot.get("pipelines", []):
        runs = p.get("runs") or {}
        if p.get("queue_status") == "disabled":
            yield finding("PL001", Severity.INFO, "Pipelines", "pipeline", p["name"], "Disabled pipeline",
                          "The pipeline is disabled.", "Clutter.", "Delete it if no longer required.", [], p.get("web_url"))
        elif not runs.get("total"):
            yield finding("PL002", Severity.LOW, "Pipelines", "pipeline", p["name"], "Pipeline not run recently",
                          f"No runs in the last {ctx.settings.history_days} days.", "Possibly obsolete.",
                          "Delete or disable unused pipelines.", [], p.get("web_url"))
        rate = runs.get("success_rate")
        if rate is not None and runs.get("total", 0) >= 3 and rate < ctx.settings.min_build_success_rate:
            yield finding("PL003", Severity.MEDIUM, "Pipelines", "pipeline", p["name"], "Low build success rate",
                          f"Success rate {rate:.0%} over {runs['total']} runs.",
                          "Unreliable feedback and wasted agent capacity.",
                          "Investigate recurring failures and flaky tests.",
                          ["Review failure analytics (Pipelines > Analytics)", "Quarantine flaky tests"], p.get("web_url"),
                          success_rate=rate)
        if p["type"] == "classic":
            yield finding("PL004", Severity.LOW, "Pipelines", "pipeline", p["name"], "Classic (designer) build pipeline",
                          "Pipeline is defined in the UI rather than as YAML.",
                          "No code review, history or reuse of pipeline changes.",
                          "Migrate to YAML pipelines.", ["Use 'Export to YAML' as a starting point",
                                                         "Commit azure-pipelines.yml and create a YAML pipeline"],
                          p.get("web_url"))
        secrets_in_ui = [v["name"] for v in p.get("variables", []) if v["is_secret"]]
        if secrets_in_ui:
            yield finding("PL005", Severity.LOW, "Security", "pipeline", p["name"], "Secrets defined on the pipeline",
                          f"Secret variables: {', '.join(secrets_in_ui)}.",
                          "Secrets scattered across pipelines are hard to rotate.",
                          "Move secrets to Key Vault-backed variable groups.", [], p.get("web_url"))
    for rel in ctx.snapshot.get("release_pipelines", []):
        yield finding("PL006", Severity.LOW, "Pipelines", "release pipeline", rel["name"], "Classic release pipeline",
                      "Deployment uses classic release management.",
                      "Classic releases receive no new features; no pipeline-as-code.",
                      "Migrate to multi-stage YAML with environments.",
                      ["Model each stage as a YAML deployment job targeting an environment",
                       "Recreate approvals as environment checks"], rel.get("web_url"))
    for key in ctx.analysis.get("yaml", {}).get("unregistered_pipelines", []):
        yield finding("PL007", Severity.INFO, "Pipelines", "yaml file", key, "Pipeline YAML not registered",
                      "A pipeline-like YAML file is not used by any pipeline definition or template.",
                      "Dead configuration or a missing pipeline.", "Register or remove the file.", [])


@rule("PL010")
def yaml_anti_patterns(ctx: RunContext):
    sev = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW, "INFO": Severity.INFO}
    category = {"AP001": "Security", "AP005": "Security", "AP006": "Security", "AP007": "Security"}
    for ap in ctx.analysis.get("anti_patterns", []):
        yield finding(ap["rule"], sev[ap["severity"]], category.get(ap["rule"], "Pipelines"), "pipeline", ap["pipeline"],
                      ap["title"], ap["detail"], AP_IMPACT.get(ap["rule"], "Pipeline maintainability and safety."),
                      ap["recommendation"], [])


@rule("PL020")
def naming(ctx: RunContext):
    for label, info in (ctx.analysis.get("naming") or {}).items():
        if info["outliers"]:
            yield finding("PL020", Severity.LOW, "Governance", label.rstrip("s").replace("_", " "), label,
                          f"{label.replace('_', ' ').title()} naming inconsistency",
                          f"Dominant style is {info['dominant_style']}; outliers: {', '.join(info['outliers'][:15])}.",
                          "Harder discovery, automation and ownership mapping.",
                          f"Adopt a naming convention ({info['dominant_style']}) and rename outliers.",
                          ["Document the convention in the wiki", "Rename outliers"], outliers=info["outliers"])


# --------------------------------------------------------------------------------- security
@rule("SE001")
def permissions(ctx: RunContext):
    security = ctx.snapshot.get("security") or {}
    admins = security.get("project_admin_users", [])
    if len(admins) > ctx.settings.max_project_admins:
        yield finding("SE001", Severity.MEDIUM, "Security", "group", "Project Administrators", "Too many project administrators",
                      f"{len(admins)} users are direct Project Administrators: {', '.join(admins[:10])}.",
                      "Broad administrative access increases the blast radius of compromised accounts.",
                      f"Reduce to at most {ctx.settings.max_project_admins}; use groups and just-in-time elevation.",
                      ["Project settings > Permissions > Project Administrators > Members",
                       "Replace individual users with an Entra ID group managed via PIM"])
    for entry in security.get("acl_entries", []):
        dangerous = DANGEROUS_PERMISSIONS.get(entry["namespace"], set()) & set(entry["allow"])
        if not dangerous:
            continue
        ident = (entry.get("identity") or "").lower()
        short = ident.rsplit("\\", 1)[-1].strip("[] ")
        if any(short.endswith(g) for g in BROAD_GROUPS):
            yield finding("SE002", Severity.HIGH, "Security", "permission", f"{entry['identity']} @ {entry['resource']}",
                          "Broad permissions granted to a wide group",
                          f"{entry['identity']} is explicitly allowed {', '.join(sorted(dangerous))} on {entry['resource']} "
                          f"({entry['namespace']}).",
                          "Every member of the group can bypass controls (e.g. force-push, bypass policies).",
                          "Remove the explicit allow; grant elevated permissions to a small admin group only.",
                          ["Open the security settings of the resource", f"Set {', '.join(sorted(dangerous))} to 'Not set'",
                           "Grant to a dedicated, reviewed group if required"], namespace=entry["namespace"])
        elif entry.get("identity_kind") == "build_identity" and dangerous & BUILD_IDENTITY_DANGEROUS:
            yield finding("SE003", Severity.HIGH, "Security", "permission", f"{entry['identity']} @ {entry['resource']}",
                          "Build identity has elevated permissions",
                          f"{entry['identity']} can {', '.join(sorted(dangerous & BUILD_IDENTITY_DANGEROUS))} on "
                          f"{entry['resource']}.",
                          "Any pipeline can use the job token to bypass branch protection or alter permissions.",
                          "Remove elevated permissions from the build service identity.",
                          ["Repositories > Security > {0}".format(entry["identity"]), "Reset the permissions to Not set"],
                          namespace=entry["namespace"])
    for user in security.get("users", []):
        age = days_between(user.get("last_accessed"), ctx.now)
        if age is not None and age > 90 and (user.get("access_level") or "").lower() not in ("stakeholder",):
            yield finding("SE010", Severity.LOW, "Security", "user", user["name"] or user["principal"], "Inactive user",
                          f"Last access {int(age)} days ago ({user.get('access_level')}).",
                          "Dormant accounts are a takeover target and consume licences.",
                          "Remove project access or downgrade the licence.", ["Organization settings > Users"])
    pat_creates = [e for e in security.get("pat_events", []) if "Create" in e["action"]]
    if pat_creates:
        actors = sorted({e["actor"] for e in pat_creates if e.get("actor")})
        yield finding("SE020", Severity.INFO if len(pat_creates) < 10 else Severity.MEDIUM, "Security", "organisation",
                      "PAT usage", "Personal access tokens in use",
                      f"{len(pat_creates)} PAT creation events in the audit window by {len(actors)} user(s).",
                      "PATs are long-lived bearer credentials that bypass conditional access.",
                      "Prefer Entra ID tokens / workload identities; restrict PAT scope and lifetime via policy.",
                      ["Organization settings > Policies > restrict full-scoped PATs and max lifetime",
                       "Review and revoke unused PATs"], actors=actors)


# ---------------------------------------------------------------------- boards, wiki, tests
@rule("BO001")
def boards(ctx: RunContext):
    m = (ctx.analysis.get("metrics") or {}).get("boards") or {}
    if m.get("total"):
        if m.get("open") and m.get("unassigned_open", 0) / m["open"] > 0.4:
            yield finding("BO001", Severity.LOW, "Boards", "boards", "Work items", "Many unassigned open work items",
                          f"{m['unassigned_open']} of {m['open']} open items have no owner.",
                          "Work is not tracked to accountable owners.", "Triage and assign open items.", [])
        if m.get("aging_open", 0) > 10:
            yield finding("BO002", Severity.LOW, "Boards", "boards", "Work items", "Aging open work items",
                          f"{m['aging_open']} open items older than {ctx.settings.stale_days} days.",
                          "Backlog noise hides priorities.", "Close or re-prioritise stale items.", [])
    for wiki in ctx.analysis.get("wiki", []):
        broken = sum(len(v) for v in wiki["broken_links"].values())
        if broken:
            yield finding("DO001", Severity.LOW, "Documentation", "wiki", wiki["wiki"], "Broken wiki links",
                          f"{broken} broken link(s) across {len(wiki['broken_links'])} page(s).",
                          "Readers hit dead ends; documentation decays.", "Fix or remove broken links.", [],
                          pages=list(wiki["broken_links"]))
        if wiki["orphans"]:
            yield finding("DO002", Severity.LOW, "Documentation", "wiki", wiki["wiki"], "Orphaned wiki pages",
                          f"{len(wiki['orphans'])} page(s) have no inbound links and no recent views.",
                          "Content is undiscoverable and probably stale.", "Link, merge or delete orphan pages.", [],
                          pages=wiki["orphans"])
    tests = (ctx.analysis.get("metrics") or {}).get("tests") or {}
    if tests.get("test_cases") and (tests.get("automation_coverage") or 0) < 0.5:
        yield finding("TE001", Severity.LOW, "Testing", "test plans", "Test plans", "Low test automation coverage",
                      f"{tests['automation_coverage']:.0%} of {tests['test_cases']} test cases are automated.",
                      "Manual regression testing slows delivery.", "Automate high-value test cases and associate them.", [])
    for feed in ctx.snapshot.get("feeds", []):
        public = [u["name"] for u in feed.get("upstream_sources", []) if u["type"] == "public"]
        if public and feed.get("package_count"):
            yield finding("AR001", Severity.LOW, "Security", "feed", feed["name"], "Feed mixes private and public upstreams",
                          f"Public upstreams: {', '.join(public)}.",
                          "Dependency-confusion attacks can substitute internal package names.",
                          "Use scoped packages/namespaces and lock package origin (upstream behaviour).",
                          ["Reserve internal package prefixes", "Review 'Allow externally sourced versions' setting"])


# --------------------------------------------------------------------------------- engine
def risk_score(findings: list[Finding]) -> dict:
    by_category: dict[str, float] = defaultdict(float)
    for f in findings:
        by_category[f.category] += f.severity.weight * CATEGORY_WEIGHTS.get(f.category, 0.5)
    raw = sum(by_category.values())
    # Saturating curve: 0 -> 0, 50 weighted points -> 34, 120 -> 63, 250 -> 88. Wide enough that
    # projects with dozens of findings still separate instead of all scoring ~100.
    score = round(100 * (1 - math.exp(-raw / RISK_SCALE)), 1)
    grade = "A" if score < 15 else "B" if score < 35 else "C" if score < 55 else "D" if score < 75 else "F"
    return {"score": score, "grade": grade, "raw": round(raw, 1),
            "by_category": {k: round(v, 1) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])}}


def build_recommendations(findings: list[Finding]) -> list[dict]:
    grouped: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        grouped[(f.rule_id, f.title)].append(f)
    recs = []
    for (rule_id, title), items in grouped.items():
        worst = min(items, key=lambda f: f.severity.rank)
        recs.append({
            "rule_id": rule_id,
            "title": title,
            "severity": worst.severity.value,
            "category": worst.category,
            "occurrences": len(items),
            "resources": sorted({f.resource for f in items}),
            "impact": worst.impact,
            "recommendation": worst.recommendation,
            "remediation": worst.remediation,
            "priority_score": worst.severity.weight * len(items) * CATEGORY_WEIGHTS.get(worst.category, 0.5),
        })
    return sorted(recs, key=lambda r: (Severity(r["severity"]).rank, -r["priority_score"]))


@register_analyser("risk", order=60, depends_on=("scoring",))
class RiskEngine(Analyser):
    """Evaluate risk rules, compute the risk score and build prioritised recommendations."""

    def analyse(self, ctx: RunContext) -> None:
        seen = set()
        for rule_id, fn in RULES.items():
            try:
                results = list(fn(ctx))
            except Exception as exc:  # a broken rule must not stop the others
                ctx.record(f"rule:{rule_id}", "error", f"{type(exc).__name__}: {exc}")
                continue
            for f in results:
                if f.id not in seen:
                    seen.add(f.id)
                    ctx.assessment.findings.append(f)
        ctx.analysis["risk"] = risk_score(ctx.assessment.findings)
        ctx.analysis["recommendations"] = build_recommendations(ctx.assessment.findings)
