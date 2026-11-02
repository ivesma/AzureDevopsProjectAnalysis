"""Synthetic 'Contoso / Platform' project served through a fake HTTP session.

``azdo-analyse --demo`` (and the test-suite) run every real collector against these canned REST
responses, so the full pipeline - collection, analysis and all exporters - can be exercised offline.
The dataset deliberately contains common problems (missing branch policies, an unapproved production
release stage, a direct production deployment, unused service connections, plain-text secrets ...).
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlparse

ORG = "contoso"
PROJECT = "Platform"
PID = "0f3a1c2e-0000-4000-8000-000000000001"
NOW = datetime.now(timezone.utc)

SC = {
    "sc-azure-prod": "6a0e2c41-1111-4a11-9a11-000000000001",
    "sc-azure-nonprod": "6a0e2c41-1111-4a11-9a11-000000000002",
    "sc-azure-legacy": "6a0e2c41-1111-4a11-9a11-000000000003",
    "sc-github": "6a0e2c41-1111-4a11-9a11-000000000004",
    "sc-acr": "6a0e2c41-1111-4a11-9a11-000000000005",
    "sc-k8s-prod": "6a0e2c41-1111-4a11-9a11-000000000006",
    "sc-unused-generic": "6a0e2c41-1111-4a11-9a11-000000000007",
}


def ago(days: float = 0, hours: float = 0) -> str:
    return (NOW - timedelta(days=days, hours=hours)).isoformat().replace("+00:00", "Z")


def ident(name: str) -> dict:
    return {"displayName": name, "uniqueName": f"{name.lower().replace(' ', '.')}@contoso.com"}


# ---------------------------------------------------------------------------------------- YAML
YAML = {
    ("platform-api", "/azure-pipelines.yml"): """\
trigger:
  branches:
    include: [main]
pr:
  branches:
    include: [main]
resources:
  repositories:
    - repository: templates
      type: git
      name: Platform/pipeline-templates
      ref: refs/tags/v2.1.0
variables:
  - group: api-common
  - name: buildConfiguration
    value: Release
extends:
  template: templates/ci-dotnet.yml@templates
  parameters:
    projectPath: src/Api
""",
    ("platform-api", "/deploy/cd.yml"): """\
trigger: none
resources:
  pipelines:
    - pipeline: ci
      source: platform-api-ci
      trigger:
        branches: [main]
  repositories:
    - repository: templates
      type: git
      name: Platform/pipeline-templates
variables:
  - group: kv-platform-secrets
stages:
  - stage: DeployDev
    jobs:
      - template: templates/deploy-webapp.yml@templates
        parameters:
          environment: dev
          serviceConnection: sc-azure-nonprod
          appName: platform-api-dev
  - stage: DeployTest
    dependsOn: DeployDev
    jobs:
      - template: templates/deploy-webapp.yml@templates
        parameters:
          environment: test
          serviceConnection: sc-azure-nonprod
          appName: platform-api-test
  - stage: DeployProd
    dependsOn: DeployTest
    condition: and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'))
    jobs:
      - template: templates/deploy-webapp.yml@templates
        parameters:
          environment: production
          serviceConnection: sc-azure-prod
          appName: platform-api
""",
    ("platform-api", "/deploy/k8s/deployment.yaml"): """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: platform-api
""",
    ("pipeline-templates", "/templates/ci-dotnet.yml"): """\
parameters:
  - name: projectPath
    type: string
  - name: dotnetVersion
    type: string
    default: '8.x'
stages:
  - stage: Build
    jobs:
      - job: Build
        pool:
          vmImage: ubuntu-latest
        steps:
          - template: steps/dotnet-build.yml
            parameters:
              projectPath: ${{ parameters.projectPath }}
  - stage: Test
    dependsOn: Build
    jobs:
      - job: UnitTests
        pool:
          vmImage: ubuntu-latest
        steps:
          - task: DotNetCoreCLI@2
            inputs:
              command: test
          - ${{ if eq(parameters.dotnetVersion, '8.x') }}:
            - task: SonarCloudAnalyze@1
""",
    ("pipeline-templates", "/templates/steps/dotnet-build.yml"): """\
parameters:
  - name: projectPath
    type: string
steps:
  - task: UseDotNet@2
    inputs:
      version: 8.x
  - task: NuGetAuthenticate@1
  - task: DotNetCoreCLI@2
    inputs:
      command: restore
      feedsToUse: select
      vstsFeed: Platform/platform-packages
  - task: DotNetCoreCLI@2
    inputs:
      command: build
      projects: ${{ parameters.projectPath }}
""",
    ("pipeline-templates", "/templates/deploy-webapp.yml"): """\
parameters:
  - name: environment
    type: string
  - name: serviceConnection
    type: string
  - name: appName
    type: string
jobs:
  - deployment: Deploy
    environment: ${{ parameters.environment }}
    pool:
      vmImage: ubuntu-latest
    strategy:
      runOnce:
        deploy:
          steps:
            - download: current
            - task: AzureWebApp@1
              inputs:
                azureSubscription: ${{ parameters.serviceConnection }}
                appName: ${{ parameters.appName }}
""",
    ("platform-web", "/azure-pipelines.yml"): """\
pool:
  vmImage: ubuntu-18.04
variables:
  apiKey: 'sk_live_51HxAbCdEf'
  nodeVersion: '18.x'
steps:
  - checkout: self
    persistCredentials: true
  - task: NodeTool@0
    inputs:
      versionSpec: $(nodeVersion)
  - script: |
      curl -sL https://example.com/install.sh | bash
      npm ci
      npm run build
    displayName: Build
  - template: templates/test-steps.yml
  - task: Npm@1
    continueOnError: true
    inputs:
      command: publish
      publishRegistry: useFeed
      publishFeed: platform-packages
  - task: Docker@2
    inputs:
      containerRegistry: sc-acr
      command: buildAndPush
      repository: platform-web
""",
    ("platform-web", "/templates/test-steps.yml"): """\
steps:
  - script: npm test
    displayName: Test
  - task: PublishTestResults@2
""",
    ("platform-web", "/old-pipeline.yml"): """\
trigger:
  - master
pool:
  vmImage: vs2017-win2016
steps:
  - script: echo legacy
""",
    ("infra-terraform", "/pipelines/infra.yml"): """\
trigger:
  branches:
    include: [main]
  paths:
    include: [terraform/*]
schedules:
  - cron: "0 3 * * 1"
    displayName: Weekly drift check
    branches:
      include: [main]
    always: true
stages:
  - stage: Plan
    jobs:
      - job: Plan
        pool: OnPrem-Pool
        steps:
          - task: TerraformTaskV4@4
            inputs:
              command: plan
              environmentServiceNameAzureRM: sc-azure-prod
  - stage: ApplyProd
    dependsOn: Plan
    jobs:
      - job: ApplyProduction
        pool: OnPrem-Pool
        steps:
          - task: AzureKeyVault@2
            inputs:
              azureSubscription: sc-azure-prod
              KeyVaultName: kv-platform-prod
          - task: TerraformTaskV4@4
            inputs:
              command: apply
              environmentServiceNameAzureRM: sc-azure-prod
""",
}
OTHER_FILES = {
    "platform-api": ["/src/Api/Api.csproj", "/Dockerfile", "/README.md"],
    "platform-web": ["/package.json", "/Dockerfile", "/.gitmodules"],
    "pipeline-templates": ["/README.md"],
    "infra-terraform": ["/terraform/main.tf", "/terraform/aks.tf"],
}
GITMODULES = '[submodule "assets"]\n\tpath = assets\n\turl = https://dev.azure.com/contoso/Platform/_git/shared-assets\n'

REPOS = [
    {"id": "repo-api", "name": "platform-api", "defaultBranch": "refs/heads/main", "size": 5_400_000},
    {"id": "repo-web", "name": "platform-web", "defaultBranch": "refs/heads/main", "size": 12_800_000},
    {"id": "repo-tpl", "name": "pipeline-templates", "defaultBranch": "refs/heads/main", "size": 90_000},
    {"id": "repo-infra", "name": "infra-terraform", "defaultBranch": "refs/heads/main", "size": 300_000},
    {"id": "repo-legacy", "name": "legacy-tools", "defaultBranch": "refs/heads/master", "size": 1_000_000,
     "isDisabled": True},
    {"id": "repo-sandbox", "name": "sandbox", "size": 0},
]
REPO_BY_ID = {r["id"]: r for r in REPOS}
AUTHORS = ["Alex Kim", "Sam Patel", "Jordan Lee", "Riley Chen", "Morgan Diaz", "Casey Novak"]


def _policy(pid: int, type_name: str, repo: str | None, settings: dict, blocking: bool = True) -> dict:
    scope = [{"repositoryId": repo, "refName": "refs/heads/main", "matchKind": "Exact"}]
    return {"id": pid, "type": {"displayName": type_name}, "isEnabled": True, "isBlocking": blocking,
            "settings": {"scope": scope, **settings}, "createdBy": ident("Alex Kim"), "createdDate": ago(400)}


POLICIES = [
    _policy(1, "Minimum number of reviewers", "repo-api", {"minimumApproverCount": 2, "creatorVoteCounts": False,
                                                           "resetOnSourcePush": True}),
    _policy(2, "Build", "repo-api", {"buildDefinitionId": 1}),
    _policy(3, "Comment requirements", "repo-api", {}),
    _policy(4, "Work item linking", "repo-api", {}),
    _policy(5, "Require a merge strategy", "repo-api", {"allowSquash": True, "allowNoFastForward": False}),
    _policy(6, "Minimum number of reviewers", "repo-web", {"minimumApproverCount": 1, "creatorVoteCounts": True}),
    _policy(7, "Minimum number of reviewers", "repo-infra", {"minimumApproverCount": 2}),
    _policy(8, "Build", "repo-infra", {"buildDefinitionId": 4}),
]

# ---------------------------------------------------------------------------- pipelines & runs
DEFINITIONS = [
    {"id": 1, "name": "platform-api-ci", "path": "\\Platform\\API", "repo": "repo-api", "yaml": "/azure-pipelines.yml",
     "pool": ("Azure Pipelines", True), "vgs": []},
    {"id": 2, "name": "platform-api-cd", "path": "\\Platform\\API", "repo": "repo-api", "yaml": "/deploy/cd.yml",
     "pool": ("Azure Pipelines", True), "vgs": []},
    {"id": 3, "name": "Platform Web CI", "path": "\\Platform\\Web", "repo": "repo-web", "yaml": "/azure-pipelines.yml",
     "pool": ("Azure Pipelines", True), "vgs": [{"id": 1, "name": "api-common"}]},
    {"id": 4, "name": "infra-deploy", "path": "\\Infrastructure", "repo": "repo-infra", "yaml": "/pipelines/infra.yml",
     "pool": ("OnPrem-Pool", False), "vgs": []},
    {"id": 5, "name": "Legacy-Build", "path": "\\Legacy", "repo": "repo-api", "yaml": None,
     "pool": ("OnPrem-Pool", False), "vgs": [{"id": 1, "name": "api-common"}]},
    {"id": 6, "name": "docs-publish", "path": "\\", "repo": "repo-web", "yaml": None,
     "pool": ("Azure Pipelines", True), "vgs": [], "disabled": True},
]
RUN_PROFILE = {1: (60, 0.92, 8), 2: (18, 0.9, 12), 3: (30, 0.55, 6), 4: (12, 0.85, 20), 5: (8, 0.75, 25)}
TASKS = [
    {"id": "71a9a2d3-a98a-4caa-96ab-affca411ecda", "name": "MSBuild", "friendlyName": "MSBuild"},
    {"id": "497d490f-eea7-4f2b-ab94-48d9c1acdcb1", "name": "AzureRmWebAppDeployment", "friendlyName": "Azure App Service deploy"},
    {"id": "2ff763a7-ce83-4e1f-bc89-0ae63477cebe", "name": "PublishBuildArtifacts", "friendlyName": "Publish build artifacts"},
    {"id": "333b11bd-d341-40d9-afcf-b32d5ce6f23b", "name": "NuGetCommand", "friendlyName": "NuGet"},
]


def _definition(d: dict) -> dict:
    repo = REPO_BY_ID[d["repo"]]
    queue = {"id": 11 if d["pool"][1] else 12, "name": d["pool"][0],
             "pool": {"id": 1 if d["pool"][1] else 10, "name": d["pool"][0], "isHosted": d["pool"][1]}}
    base = {
        "id": d["id"], "name": d["name"], "path": d["path"], "revision": 7, "queue": queue,
        "repository": {"id": repo["id"], "name": repo["name"], "type": "TfsGit", "defaultBranch": "refs/heads/main"},
        "authoredBy": ident("Sam Patel"), "createdDate": ago(500 - d["id"] * 30),
        "variableGroups": d["vgs"], "queueStatus": "disabled" if d.get("disabled") else "enabled",
        "_links": {"web": {"href": f"https://dev.azure.com/{ORG}/{PROJECT}/_build?definitionId={d['id']}"}},
        "retentionRules": [],
    }
    if d["yaml"]:
        base["process"] = {"type": 2, "yamlFilename": d["yaml"]}
        base["triggers"] = [{"triggerType": "continuousIntegration", "settingsSourceType": 2,
                             "branchFilters": [], "pathFilters": []}]
        base["variables"] = {"system.debug": {"value": "false", "allowOverride": True}} if d["id"] == 1 else {}
    else:
        base["process"] = {"type": 1, "phases": [{"name": "Agent job 1", "steps": [
            {"displayName": "Build solution", "task": {"id": TASKS[0]["id"], "versionSpec": "1.*"},
             "inputs": {"solution": "**/*.sln"}, "enabled": True},
            {"displayName": "Publish artifact", "task": {"id": TASKS[2]["id"], "versionSpec": "1.*"},
             "inputs": {}, "enabled": True, "continueOnError": True},
            {"displayName": "NuGet push", "task": {"id": TASKS[3]["id"], "versionSpec": "2.*"},
             "inputs": {"command": "push", "publishVstsFeed": "platform-packages"}, "enabled": True},
        ]}]}
        base["variables"] = {"BuildPlatform": {"value": "Any CPU"}, "SigningPassword": {"isSecret": True}}
        base["demands"] = ["msbuild", "visualstudio"]
        base["triggers"] = [{"triggerType": "continuousIntegration", "branchFilters": ["+refs/heads/master"]}]
        if d["id"] == 6:
            base["triggers"] = [{"triggerType": "buildCompletion", "definition": {"id": 1, "name": "platform-api-ci"},
                                 "branchFilters": ["+refs/heads/main"]}]
    return base


def _builds() -> list[dict]:
    rnd = random.Random(42)
    builds, bid = [], 1000
    for d in DEFINITIONS:
        count, rate, minutes = RUN_PROFILE.get(d["id"], (0, 0, 0))
        definition = _definition(d)
        for i in range(count):
            bid += 1
            queued = NOW - timedelta(days=88 * (i + 0.5) / max(count, 1), hours=rnd.random() * 6)
            start = queued + timedelta(minutes=1)
            finish = start + timedelta(minutes=minutes * (0.7 + rnd.random() * 0.6))
            result = "succeeded" if rnd.random() < rate else rnd.choice(["failed", "failed", "canceled"])
            builds.append({
                "id": bid, "buildNumber": f"2026.{bid}", "definition": {"id": d["id"], "name": d["name"]},
                "status": "completed", "result": result, "reason": "individualCI",
                "queueTime": queued.isoformat(), "startTime": start.isoformat(), "finishTime": finish.isoformat(),
                "sourceBranch": "refs/heads/main", "requestedFor": ident(rnd.choice(AUTHORS)),
                "queue": definition["queue"],
            })
    return sorted(builds, key=lambda b: b["finishTime"], reverse=True)


def _commits(repo_id: str) -> list[dict]:
    rnd = random.Random(sum(map(ord, repo_id)))
    if repo_id == "repo-infra":
        return []
    count = {"repo-api": 140, "repo-web": 90, "repo-tpl": 15}.get(repo_id, 5)
    out = []
    for i in range(count):
        author = rnd.choice(AUTHORS[:4] if repo_id != "repo-web" else AUTHORS)
        when = (NOW - timedelta(days=85 * i / count, hours=rnd.random() * 12)).isoformat()
        sig = {"name": author, "email": f"{author.lower().replace(' ', '.')}@contoso.com", "date": when}
        out.append({"commitId": f"{repo_id}-{i:04d}", "author": sig, "committer": sig, "comment": f"Change {i}"})
    return out


def _prs(repo_id: str) -> list[dict]:
    rnd = random.Random(len(repo_id))
    out = []
    for i in range({"repo-api": 40, "repo-web": 25, "repo-tpl": 6}.get(repo_id, 0)):
        created = NOW - timedelta(days=88 * i / 40 + 1)
        status = "completed" if i % 7 else ("active" if i % 2 else "abandoned")
        pr = {"pullRequestId": i + 1, "status": status, "creationDate": created.isoformat(),
              "createdBy": ident(rnd.choice(AUTHORS)),
              "reviewers": [{**ident(rnd.choice(AUTHORS)), "vote": 10}]}
        if status == "completed":
            pr["closedDate"] = (created + timedelta(hours=4 + rnd.random() * 40)).isoformat()
            pr["completionOptions"] = {"mergeStrategy": "squash" if repo_id == "repo-api" else "noFastForward"}
        out.append(pr)
    return out


# ----------------------------------------------------------------------------------- releases
def _release_definition() -> dict:
    def stage(sid, name, rank, conditions, approved=False, gates=False):
        return {
            "id": sid, "name": name, "rank": rank, "owner": ident("Morgan Diaz"),
            "conditions": conditions,
            "preDeployApprovals": {"approvals": [{"isAutomated": not approved, "approver": ident("Release Managers")
                                                  if approved else None}],
                                   "approvalOptions": {"releaseCreatorCanBeApprover": True}},
            "postDeployApprovals": {"approvals": [{"isAutomated": True}]},
            "preDeploymentGates": {"gates": [{"tasks": [{"name": "Query Azure Monitor alerts", "enabled": True}]}]
                                   if gates else [], "gatesOptions": {"isEnabled": gates}},
            "postDeploymentGates": {"gates": []},
            "deployPhases": [{"name": "Agent job", "phaseType": 1, "deploymentInput": {"queueId": 12, "demands": []},
                              "workflowTasks": [{"taskId": TASKS[1]["id"], "version": "4.*", "name": "Deploy App Service",
                                                 "enabled": True, "inputs": {"ConnectedServiceName": SC["sc-azure-legacy"],
                                                                             "WebAppName": f"legacy-{name.lower()}"}}]}],
            "variables": {"AppSecret": {"isSecret": True}} if name == "Production" else {},
            "variableGroups": [],
            "retentionPolicy": {"daysToKeep": 30},
        }

    return {
        "id": 1, "name": "Legacy Release", "path": "\\Legacy", "description": "Classic release of Legacy-Build",
        "createdBy": ident("Morgan Diaz"), "createdOn": ago(700), "modifiedBy": ident("Morgan Diaz"),
        "modifiedOn": ago(40),
        "_links": {"web": {"href": f"https://dev.azure.com/{ORG}/{PROJECT}/_release?definitionId=1"}},
        "artifacts": [{"alias": "_Legacy-Build", "type": "Build", "isPrimary": True,
                       "definitionReference": {"definition": {"id": 5, "name": "Legacy-Build"}}}],
        "triggers": [{"triggerType": 1, "artifactAlias": "_Legacy-Build"}],
        "variables": {"Environment": {"value": "x"}},
        "variableGroups": [1],
        "environments": [
            stage(11, "Dev", 1, [{"conditionType": 1, "name": "ReleaseStarted"}]),
            stage(12, "QA", 2, [{"conditionType": 2, "name": "Dev", "value": "4"}], approved=True),
            stage(13, "Production", 3, [{"conditionType": 2, "name": "QA", "value": "4"}]),
        ],
    }


# ------------------------------------------------------------------------------- environments
ENVIRONMENTS = [
    {"id": 1, "name": "dev"}, {"id": 2, "name": "test"}, {"id": 3, "name": "production"}, {"id": 4, "name": "staging"},
]


def _env_records(eid: int) -> list[dict]:
    if eid == 4:
        return []
    count = {1: 18, 2: 15, 3: 9}[eid]
    stage = {1: "DeployDev", 2: "DeployTest", 3: "DeployProd"}[eid]
    return [{"id": eid * 100 + i, "definition": {"id": 2, "name": "platform-api-cd"}, "owner": {"name": f"2026.{i}"},
             "stageName": stage, "jobName": "Deploy", "result": "failed" if (eid == 3 and i == 2) else "succeeded",
             "startTime": ago(80 * i / count + 0.5), "finishTime": ago(80 * i / count + 0.4)} for i in range(count)]


# ------------------------------------------------------------------------------------- boards
WIT_STATES = {
    "Epic": [("New", "Proposed"), ("Active", "InProgress"), ("Resolved", "Resolved"), ("Closed", "Completed")],
    "Feature": [("New", "Proposed"), ("Active", "InProgress"), ("Resolved", "Resolved"), ("Closed", "Completed")],
    "User Story": [("New", "Proposed"), ("Active", "InProgress"), ("Resolved", "Resolved"), ("Closed", "Completed"),
                   ("Removed", "Removed")],
    "Bug": [("New", "Proposed"), ("Active", "InProgress"), ("Resolved", "Resolved"), ("Closed", "Completed")],
    "Task": [("New", "Proposed"), ("Active", "InProgress"), ("Closed", "Completed"), ("Removed", "Removed")],
}


def _work_items() -> list[dict]:
    rnd = random.Random(7)
    items, next_id = [], 100
    plan = [("Epic", 2, None), ("Feature", 6, "Epic"), ("User Story", 24, "Feature"), ("Bug", 12, "User Story"),
            ("Task", 30, "User Story")]
    by_type: dict[str, list[int]] = {}
    for wtype, count, parent_type in plan:
        for _ in range(count):
            next_id += 1
            age = rnd.uniform(5, 420)
            closed = rnd.random() < 0.6
            fields = {
                "System.Id": next_id, "System.WorkItemType": wtype, "System.Title": f"{wtype} {next_id}",
                "System.State": "Closed" if closed else rnd.choice(["New", "Active"]),
                "System.CreatedDate": ago(age), "System.ChangedDate": ago(max(age - 3, 0)),
                "System.AreaPath": "Platform\\API" if rnd.random() < 0.5 else "Platform\\Web",
                "System.IterationPath": "Platform\\Sprint 42", "System.Tags": "backend; api" if rnd.random() < 0.3 else "",
                "Microsoft.VSTS.Common.Priority": rnd.randint(1, 3),
            }
            if rnd.random() < 0.8:
                fields["System.AssignedTo"] = ident(rnd.choice(AUTHORS))
            if closed or fields["System.State"] == "Active":
                fields["Microsoft.VSTS.Common.ActivatedDate"] = ago(age * rnd.uniform(0.4, 0.9))
            if closed:
                fields["Microsoft.VSTS.Common.ClosedDate"] = ago(age * rnd.uniform(0.05, 0.35))
            if parent_type and by_type.get(parent_type):
                fields["System.Parent"] = rnd.choice(by_type[parent_type])
            by_type.setdefault(wtype, []).append(next_id)
            items.append({"id": next_id, "fields": fields})
    return items


WORK_ITEMS = _work_items()

# --------------------------------------------------------------------------------------- wiki
WIKI_PAGES = {
    "/Home": "Welcome. See [Architecture](/Architecture) and [Onboarding](/Onboarding).",
    "/Architecture": "Overview: [overview](/Architecture/Overview). Decisions: [ADRs](/Architecture/Decisions).",
    "/Architecture/Overview": "System context diagram. Back to [home](/Home).",
    "/Architecture/Decisions": "List of decisions: [ADR 001](/Architecture/Decisions/ADR-001)",
    "/Architecture/Decisions/ADR-001": "Use AKS for hosting.",
    "/Onboarding": "Read [overview](/Architecture/Overview) and the [runbook](/Runbooks/Missing-Page).",
    "/Old-Notes": "Notes from 2022 that nobody reads.",
}


def _wiki_tree() -> dict:
    def node(path: str) -> dict:
        children = [p for p in WIKI_PAGES if p.rsplit("/", 1)[0] == path and p != path]
        return {"path": path, "order": 0, "gitItemPath": path.replace(" ", "-") + ".md" if path != "/" else "/",
                "isParentPage": bool(children), "subPages": [node(c) for c in children]}

    root = {"path": "/", "subPages": [node(p) for p in WIKI_PAGES if p.count("/") == 1]}
    return root


# ----------------------------------------------------------------------------------- security
GIT_ACTIONS = ["Administer", "GenericRead", "GenericContribute", "ForcePush", "CreateBranch", "CreateTag", "ManageNote",
               "PolicyExempt", "CreateRepository", "DeleteRepository", "RenameRepository", "EditPolicies",
               "RemoveOthersLocks", "ManagePermissions", "PullRequestContribute", "PullRequestBypassPolicy"]
ADMINS = ["Alex Kim", "Sam Patel", "Jordan Lee", "Riley Chen", "Morgan Diaz", "Casey Novak", "Taylor Brooks"]
SUBJECTS = {f"aad.{n.split()[0].lower()}": {"subjectKind": "user", "displayName": n,
                                             "principalName": f"{n.lower().replace(' ', '.')}@contoso.com",
                                             "origin": "aad"} for n in ADMINS}
SUBJECTS.update({
    "vssgp.team": {"subjectKind": "group", "displayName": "Platform Team", "principalName": "[Platform]\\Platform Team"},
    "svc.build": {"subjectKind": "user", "displayName": "Platform Build Service (contoso)",
                  "principalName": "build@svc"},
    "aadsp.terraform": {"subjectKind": "servicePrincipal", "displayName": "spn-terraform", "principalName": "spn-terraform"},
})
GROUPS = [
    ("vssgp.admins", "Project Administrators", [f"aad.{n.split()[0].lower()}" for n in ADMINS]),
    ("vssgp.contrib", "Contributors", ["vssgp.team", "svc.build", "aadsp.terraform"]),
    ("vssgp.readers", "Readers", ["aad.casey"]),
    ("vssgp.buildadmins", "Build Administrators", ["aad.alex"]),
    ("vssgp.team", "Platform Team", ["aad.jordan", "aad.riley", "aad.morgan"]),
    ("vssgp.approvers", "Release Approvers", ["aad.morgan", "aad.taylor"]),
]


def _bits(*names: str) -> int:
    return sum(1 << GIT_ACTIONS.index(n) for n in names)


# ------------------------------------------------------------------------------------- router
class FakeResponse:
    def __init__(self, status: int, data: Any = None, headers: dict | None = None):
        self.status_code = status
        self._data = data
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.content = b"" if data is None else json.dumps(data).encode()
        self.text = self.content.decode()

    def json(self) -> Any:
        return self._data


Route = tuple[str, str, str, Callable[..., Any]]


class DemoSession:
    """Implements ``session.request`` for the AzureDevOpsClient using the synthetic dataset."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.published: dict[str, str] = {}
        self.builds = _builds()
        self.routes: list[tuple[str, str, re.Pattern, Callable[..., Any]]] = []
        for service, method, pattern, handler in self._route_table():
            self.routes.append((service, method, re.compile(f"^{pattern}$"), handler))

    def request(self, method: str, url: str, params: dict | None = None, json: Any = None, headers: dict | None = None,
                **_: Any) -> FakeResponse:
        parsed = urlparse(url)
        host = parsed.netloc.split(".")[0]
        service = "core" if host == "dev" else host
        path = unquote(parsed.path).removeprefix(f"/{ORG}/").strip("/")
        self.calls.append((method, f"{service}:{path}"))
        for svc, m, rx, handler in self.routes:
            if svc == service and m == method and (match := rx.match(path)):
                result = handler(*match.groups(), params=params or {}, body=json)
                if isinstance(result, FakeResponse):
                    return result
                if isinstance(result, list):
                    result = {"count": len(result), "value": result}
                return FakeResponse(200, result)
        return FakeResponse(404, {"message": f"demo: no route for {method} {service}:{path}"})

    # ------------------------------------------------------------------ handlers
    def _route_table(self) -> list[Route]:
        P = PROJECT
        return [
            ("core", "GET", f"_apis/projects/{P}", self.project),
            ("core", "GET", r"_apis/projects/[^/]+/properties", lambda **_: [
                {"name": "System.Process Template", "value": "Agile"}]),
            ("core", "GET", r"_apis/projects/[^/]+/teams", lambda **_: [
                {"id": "team-1", "name": "Platform Team", "description": "Default team"},
                {"id": "team-2", "name": "SRE", "description": "Reliability"}]),
            ("core", "GET", r"_apis/projects/[^/]+/teams/([^/]+)/members", self.team_members),
            ("core", "GET", f"{P}/_apis/git/repositories", lambda **_: REPOS),
            ("core", "GET", f"{P}/_apis/policy/configurations", lambda **_: POLICIES),
            ("core", "GET", f"{P}/_apis/git/repositories/([^/]+)/refs", self.refs),
            ("core", "GET", f"{P}/_apis/git/repositories/([^/]+)/commits", self.commits),
            ("core", "GET", f"{P}/_apis/git/repositories/([^/]+)/pullrequests", self.pull_requests),
            ("core", "GET", f"{P}/_apis/git/repositories/([^/]+)/items", self.items),
            ("core", "GET", "_apis/distributedtask/tasks", lambda **_: TASKS),
            ("core", "GET", f"{P}/_apis/build/definitions", lambda **_: [
                {"id": d["id"], "name": d["name"]} for d in DEFINITIONS]),
            ("core", "GET", rf"{P}/_apis/build/definitions/(\d+)", lambda i, **_: _definition(
                next(d for d in DEFINITIONS if d["id"] == int(i)))),
            ("core", "GET", f"{P}/_apis/build/builds", lambda **_: self.builds),
            ("vsrm", "GET", f"{P}/_apis/release/definitions", lambda **_: [{"id": 1, "name": "Legacy Release"}]),
            ("vsrm", "GET", rf"{P}/_apis/release/definitions/(\d+)", lambda i, **_: _release_definition()),
            ("vsrm", "GET", f"{P}/_apis/release/deployments", self.release_deployments),
            ("core", "GET", f"{P}/_apis/distributedtask/environments", lambda **_: [
                {**e, "createdBy": ident("Morgan Diaz"), "createdOn": ago(300)} for e in ENVIRONMENTS]),
            ("core", "GET", rf"{P}/_apis/distributedtask/environments/(\d+)", self.environment),
            ("core", "GET", rf"{P}/_apis/distributedtask/environments/(\d+)/providers/kubernetes/(\d+)",
             lambda e, r, **_: {"namespace": "platform", "clusterName": "aks-prod-weu",
                                "serviceEndpointId": SC["sc-k8s-prod"]}),
            ("core", "GET", rf"{P}/_apis/distributedtask/environments/(\d+)/providers/virtualmachines",
             lambda e, **_: [{"id": 1, "name": "vm-test-01", "tags": ["web"], "agent": {"status": "online",
                                                                                       "version": "3.248.0"}}]
             if e == "2" else []),
            ("core", "GET", rf"{P}/_apis/distributedtask/environments/(\d+)/environmentdeploymentrecords",
             lambda e, **_: _env_records(int(e))),
            ("core", "GET", f"{P}/_apis/pipelines/checks/configurations", self.checks),
            ("core", "GET", f"{P}/_apis/pipelines/pipelinePermissions/([^/]+)/([^/]+)", self.permissions),
            ("core", "GET", f"{P}/_apis/distributedtask/queues", lambda **_: [
                {"id": 11, "name": "Azure Pipelines", "pool": {"id": 1}},
                {"id": 12, "name": "OnPrem-Pool", "pool": {"id": 10}},
                {"id": 13, "name": "Empty-Pool", "pool": {"id": 11}}]),
            ("core", "GET", "_apis/distributedtask/pools", lambda **_: [
                {"id": 1, "name": "Azure Pipelines", "isHosted": True, "size": 0},
                {"id": 10, "name": "OnPrem-Pool", "isHosted": False, "size": 2, "autoUpdate": False,
                 "owner": ident("Alex Kim")},
                {"id": 11, "name": "Empty-Pool", "isHosted": False, "size": 0},
                {"id": 99, "name": "Other-Project-Pool", "isHosted": False, "size": 4}]),
            ("core", "GET", r"_apis/distributedtask/pools/(\d+)/agents", self.agents),
            ("core", "GET", f"{P}/_apis/distributedtask/variablegroups", self.variable_groups),
            ("core", "GET", f"{P}/_apis/distributedtask/securefiles", lambda **_: [
                {"id": "sf-1", "name": "signing-cert.pfx", "createdBy": ident("Alex Kim"), "createdOn": ago(500),
                 "modifiedOn": ago(500)}]),
            ("core", "GET", f"{P}/_apis/serviceendpoint/endpoints", self.endpoints),
            ("core", "GET", f"{P}/_apis/serviceendpoint/([^/]+)/executionhistory", self.execution_history),
            ("core", "GET", f"{P}/_apis/wit/workitemtypes", lambda **_: [
                {"name": n, "referenceName": f"Microsoft.VSTS.WorkItemTypes.{n.replace(' ', '')}"} for n in WIT_STATES]),
            ("core", "GET", f"{P}/_apis/wit/workitemtypes/([^/]+)/states", lambda t, **_: [
                {"name": s, "category": c} for s, c in WIT_STATES.get(t, [])]),
            ("core", "GET", f"{P}/[^/]+/_apis/work/backlogconfiguration", lambda **_: {
                "portfolioBacklogs": [{"name": "Features", "workItemTypes": [{"name": "Feature"}]},
                                      {"name": "Epics", "workItemTypes": [{"name": "Epic"}]}],
                "requirementBacklog": {"name": "Stories", "workItemTypes": [{"name": "User Story"}, {"name": "Bug"}]},
                "taskBacklog": {"name": "Tasks", "workItemTypes": [{"name": "Task"}]}}),
            ("core", "POST", f"{P}/_apis/wit/wiql", self.wiql),
            ("core", "POST", "_apis/wit/workitemsbatch", self.work_items_batch),
            ("core", "GET", f"{P}/_apis/wiki/wikis", lambda **_: [
                {"id": "wiki-1", "name": "Platform.wiki", "type": "projectWiki", "repositoryId": "wiki-repo",
                 "mappedPath": "/", "remoteUrl": f"https://dev.azure.com/{ORG}/{P}/_wiki/wikis/Platform.wiki"}]),
            ("core", "GET", f"{P}/_apis/wiki/wikis/([^/]+)/pages", self.wiki_pages),
            ("core", "PUT", f"{P}/_apis/wiki/wikis/([^/]+)/pages", self.wiki_put),
            ("core", "POST", f"{P}/_apis/wiki/wikis/([^/]+)/pagesbatch", lambda w, **_: [
                {"path": p, "viewStats": [{"count": 0 if p == "/Old-Notes" else 12}]} for p in WIKI_PAGES]),
            ("core", "GET", f"{P}/_apis/testplan/plans", lambda **_: [
                {"id": 1, "name": "Release 2026.4", "state": "Active", "areaPath": "Platform",
                 "iteration": "Platform\\Sprint 42", "owner": ident("Riley Chen")}]),
            ("core", "GET", rf"{P}/_apis/testplan/Plans/(\d+)/suites", lambda p, **_: [
                {"id": 2, "name": "Release 2026.4", "suiteType": "staticTestSuite"},
                {"id": 3, "name": "API regression", "suiteType": "staticTestSuite", "parentSuite": {"id": 2}},
                {"id": 4, "name": "UI smoke", "suiteType": "requirementTestSuite", "parentSuite": {"id": 2}}]),
            ("core", "GET", rf"{P}/_apis/testplan/Plans/(\d+)/Suites/(\d+)/TestCase", self.test_cases),
            ("core", "GET", f"{P}/_apis/test/runs", lambda **_: [
                {"id": i, "isAutomated": i % 3 != 0, "totalTests": 40, "passedTests": 37 if i % 4 else 30}
                for i in range(1, 25)]),
            ("feeds", "GET", f"{P}/_apis/packaging/feeds", lambda **_: [
                {"id": "feed-1", "name": "platform-packages", "project": {"id": PID}, "upstreamEnabled": True,
                 "upstreamSources": [{"name": "NuGet Gallery", "protocol": "nuget",
                                      "location": "https://api.nuget.org/v3/index.json",
                                      "upstreamSourceType": "public"}]}]),
            ("feeds", "GET", "_apis/packaging/feeds", lambda **_: [
                {"id": "feed-1", "name": "platform-packages", "project": {"id": PID}},
                {"id": "feed-org", "name": "org-shared", "upstreamSources": []}]),
            ("feeds", "GET", r"(?:Platform/)?_apis/packaging/feeds/([^/]+)/packages", self.packages),
            ("feeds", "GET", r"(?:Platform/)?_apis/packaging/feeds/([^/]+)/permissions", lambda f, **_: [
                {"displayName": "[Platform]\\Contributors", "role": "contributor"},
                {"displayName": "Platform Build Service (contoso)", "role": "collaborator"}]),
            ("feeds", "GET", r"(?:Platform/)?_apis/packaging/feeds/([^/]+)/retentionpolicies", lambda f, **_: {
                "countLimit": 20, "daysToKeepRecentlyDownloadedPackages": 30}),
            ("vssps", "GET", r"_apis/graph/descriptors/[^/]+", lambda **_: {"value": "scp.platform"}),
            ("vssps", "GET", "_apis/graph/groups", lambda **_: [
                {"descriptor": d, "displayName": n, "principalName": f"[Platform]\\{n}", "origin": "vsts"}
                for d, n, _ in GROUPS]),
            ("vssps", "GET", "_apis/graph/Memberships/([^/]+)", self.memberships),
            ("vssps", "POST", "_apis/graph/subjectlookup", lambda body, **_: {
                "value": {k["descriptor"]: {**SUBJECTS.get(k["descriptor"], {}), "descriptor": k["descriptor"]}
                          for k in body["lookupKeys"]}}),
            ("vssps", "GET", "_apis/identities", self.identities),
            ("core", "GET", "_apis/securitynamespaces", lambda **_: [
                {"namespaceId": "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87", "name": "Git Repositories",
                 "actions": [{"bit": 1 << i, "name": n} for i, n in enumerate(GIT_ACTIONS)]}]),
            ("core", "GET", "_apis/accesscontrollists/([^/]+)", self.acls),
            ("vsaex", "GET", "_apis/userentitlements", lambda **_: {"members": [
                {"user": {"displayName": n, "principalName": f"{n.lower().replace(' ', '.')}@contoso.com"},
                 "accessLevel": {"licenseDisplayName": "Basic"},
                 "lastAccessedDate": ago(200 if n == "Taylor Brooks" else 2), "dateCreated": ago(800)}
                for n in ADMINS]}),
            ("auditservice", "GET", "_apis/audit/auditlog", lambda **_: {"decoratedAuditLogEntries": [
                {"actionId": "Token.PatCreateEvent", "timestamp": ago(i * 9), "actorDisplayName": AUTHORS[i % 3],
                 "area": "Token", "details": "Created PAT with full scope"} for i in range(3)] + [
                {"actionId": "Policy.PolicyConfigModified", "timestamp": ago(12), "actorDisplayName": "Alex Kim",
                 "area": "Policy", "details": "Modified branch policy"}], "hasMore": False}),
        ]

    @staticmethod
    def project(**_: Any) -> dict:
        return {"id": PID, "name": PROJECT, "description": "Shared platform services (demo dataset)",
                "url": f"https://dev.azure.com/{ORG}/_apis/projects/{PID}", "state": "wellFormed",
                "visibility": "private", "revision": 42, "lastUpdateTime": ago(3),
                "capabilities": {"processTemplate": {"templateName": "Agile", "templateTypeId": "adcc42ab"},
                                 "versioncontrol": {"sourceControlType": "Git"}},
                "defaultTeam": {"id": "team-1", "name": "Platform Team"}}

    @staticmethod
    def team_members(team: str, **_: Any) -> list[dict]:
        names = AUTHORS[:4] if team == "team-1" else AUTHORS[3:]
        return [{"identity": ident(n), "isTeamAdmin": i == 0} for i, n in enumerate(names)]

    @staticmethod
    def refs(repo: str, **_: Any) -> list[dict]:
        branches = {"repo-api": ["main", "develop", "feature/auth", "release/2026.4"],
                    "repo-web": ["main", "feature/redesign"], "repo-tpl": ["main"], "repo-infra": ["main"]}
        return [{"name": f"refs/heads/{b}", "isLocked": b.startswith("release/")} for b in branches.get(repo, ["main"])]

    @staticmethod
    def commits(repo: str, params: dict, **_: Any) -> list[dict]:
        if "searchCriteria.itemPath" in params:  # wiki page history
            return [{"author": {"name": "Riley Chen", "date": ago(30)}}]
        if repo == "repo-infra" and "searchCriteria.fromDate" not in params:
            sig = {"name": "Casey Novak", "email": "casey.novak@contoso.com", "date": ago(250)}
            return [{"commitId": "old", "author": sig, "committer": sig}]
        data = _commits(repo)
        skip, top = int(params.get("searchCriteria.$skip", 0)), int(params.get("searchCriteria.$top", 100))
        return data[skip:skip + top]

    @staticmethod
    def pull_requests(repo: str, params: dict, **_: Any) -> list[dict]:
        data = _prs(repo)
        skip, top = int(params.get("$skip", 0)), int(params.get("$top", 100))
        return data[skip:skip + top]

    @staticmethod
    def items(repo: str, params: dict, **_: Any) -> Any:
        name = REPO_BY_ID.get(repo, {}).get("name", repo)
        if params.get("recursionLevel") == "Full":
            paths = [p for (r, p) in YAML if r == name] + OTHER_FILES.get(name, [])
            return [{"path": "/", "isFolder": True}] + [{"path": p, "gitObjectType": "blob"} for p in paths]
        path = params.get("path")
        if path == "/.gitmodules" and name == "platform-web":
            return {"path": path, "content": GITMODULES}
        content = YAML.get((name, path))
        if content is None:
            return FakeResponse(404, {"message": "not found"})
        return {"path": path, "content": content}

    def release_deployments(self, **_: Any) -> list[dict]:
        out = []
        for i in range(12):
            stage = ["Dev", "QA", "Production"][i % 3]
            out.append({"id": i, "releaseDefinition": {"id": 1, "name": "Legacy Release"}, "release": {"name": f"R-{i}"},
                        "releaseEnvironment": {"name": stage}, "deploymentStatus": "succeeded" if i != 5 else "failed",
                        "startedOn": ago(7 * i + 1), "completedOn": ago(7 * i + 0.9), "requestedFor": ident("Morgan Diaz")})
        return out

    @staticmethod
    def environment(eid: str, **_: Any) -> dict:
        env = next(e for e in ENVIRONMENTS if e["id"] == int(eid))
        resources = []
        if eid == "3":
            resources = [{"id": 1, "name": "aks-prod", "type": "kubernetes", "tags": ["prod"]}]
        return {**env, "resources": resources}

    @staticmethod
    def checks(params: dict, **_: Any) -> list[dict]:
        if params.get("resourceType") == "environment" and params.get("resourceId") == "3":
            return [
                {"id": 1, "type": {"name": "Approval"}, "timeout": 4320,
                 "settings": {"approvers": [{"displayName": "[Platform]\\Release Approvers"}], "minRequiredApprovers": 1,
                              "requesterCannotBeApprover": False}},
                {"id": 2, "type": {"name": "Task Check"}, "timeout": 60,
                 "settings": {"displayName": "Branch control", "definitionRef": {"name": "evaluatebranchProtection"}}},
            ]
        if params.get("resourceType") == "environment" and params.get("resourceId") == "2":
            return [{"id": 3, "type": {"name": "Task Check"}, "settings": {"displayName": "Business Hours",
                                                                            "definitionRef": {"name": "businessHours"}}}]
        return []

    @staticmethod
    def permissions(rtype: str, rid: str, **_: Any) -> dict:
        open_resources = {("environment", "1"), ("endpoint", SC["sc-acr"]), ("variablegroup", "1"),
                          ("securefile", "sf-1"), ("queue", "12")}
        if (rtype, rid) in open_resources:
            return {"allPipelines": {"authorized": True}, "pipelines": []}
        return {"allPipelines": None, "pipelines": [{"id": 2, "authorized": True}]}

    @staticmethod
    def agents(pool: str, **_: Any) -> list[dict]:
        if pool != "10":
            return []
        return [
            {"id": 1, "name": "build-01", "version": "3.248.0", "status": "online", "enabled": True,
             "osDescription": "Ubuntu 22.04", "systemCapabilities": {"Agent.OS": "Linux", "Agent.Version": "3.248.0"},
             "userCapabilities": {"terraform": "1.9.5"}},
            {"id": 2, "name": "build-02", "version": "3.220.5", "status": "offline", "enabled": True,
             "osDescription": "Windows Server 2019", "systemCapabilities": {"Agent.OS": "Windows_NT"},
             "userCapabilities": {"msbuild": "16.0", "visualstudio": "2019"}},
        ]

    @staticmethod
    def variable_groups(**_: Any) -> list[dict]:
        return [
            {"id": 1, "name": "api-common", "type": "Vsts", "description": "Shared API settings",
             "variables": {"ApiUrl": {"value": "https://api.contoso.com"}, "ApiToken": {"isSecret": True}},
             "createdBy": ident("Sam Patel"), "createdOn": ago(400), "modifiedBy": ident("Sam Patel"),
             "modifiedOn": ago(20)},
            {"id": 2, "name": "kv-platform-secrets", "type": "AzureKeyVault",
             "providerData": {"serviceEndpointId": SC["sc-azure-prod"], "vault": "kv-platform-prod"},
             "variables": {"DbPassword": {}, "StorageKey": {}}, "createdBy": ident("Alex Kim"), "createdOn": ago(300)},
            {"id": 3, "name": "unused-legacy-settings", "type": "Vsts",
             "variables": {"Setting1": {"value": "x"}}, "createdBy": ident("Casey Novak"), "createdOn": ago(900)},
        ]

    @staticmethod
    def endpoints(**_: Any) -> list[dict]:
        def ep(name, typ, scheme, **data):
            return {"id": SC[name], "name": name, "type": typ, "url": "https://management.azure.com/",
                    "authorization": {"scheme": scheme}, "data": data, "isReady": True, "isShared": False,
                    "createdBy": ident("Alex Kim"), "owner": "Library"}

        return [
            ep("sc-azure-prod", "azurerm", "WorkloadIdentityFederation", subscriptionName="Contoso Production",
               scopeLevel="Subscription", creationMode="Manual"),
            ep("sc-azure-nonprod", "azurerm", "WorkloadIdentityFederation", subscriptionName="Contoso NonProd",
               scopeLevel="Subscription", creationMode="Automatic"),
            ep("sc-azure-legacy", "azurerm", "ServicePrincipal", subscriptionName="Contoso Legacy",
               scopeLevel="Subscription", creationMode="Manual"),
            ep("sc-github", "github", "Token"),
            ep("sc-acr", "dockerregistry", "ServicePrincipal", registrytype="ACR"),
            ep("sc-k8s-prod", "kubernetes", "Kubeconfig", authorizationType="Kubeconfig"),
            ep("sc-unused-generic", "generic", "UsernamePassword"),
        ]

    @staticmethod
    def execution_history(eid: str, **_: Any) -> list[dict]:
        usage = {
            SC["sc-azure-prod"]: [(2, "platform-api-cd", "Build"), (4, "infra-deploy", "Build")],
            SC["sc-azure-nonprod"]: [(2, "platform-api-cd", "Build")],
            SC["sc-azure-legacy"]: [(1, "Legacy Release", "Release")],
            SC["sc-acr"]: [(3, "Platform Web CI", "Build")],
        }.get(eid, [])
        return [{"data": {"definition": {"id": d, "name": n}, "planType": t, "result": "succeeded",
                          "startTime": ago(i * 3 + 1), "finishTime": ago(i * 3 + 0.9)}}
                for i, (d, n, t) in enumerate(usage * 3)]

    @staticmethod
    def wiql(body: dict, params: dict, **_: Any) -> dict:
        ids = [w["id"] for w in WORK_ITEMS]
        if "ASC" in body.get("query", ""):
            return {"workItems": [{"id": min(ids)}]}
        return {"workItems": [{"id": i} for i in ids[: int(params.get("$top", 20000))]]}

    @staticmethod
    def work_items_batch(body: dict, **_: Any) -> list[dict]:
        wanted = set(body.get("ids", []))
        return [w for w in WORK_ITEMS if w["id"] in wanted]

    @staticmethod
    def wiki_pages(wid: str, params: dict, **_: Any) -> Any:
        path = params.get("path", "/")
        if params.get("recursionLevel") == "full":
            return _wiki_tree()
        if path in WIKI_PAGES:
            return FakeResponse(200, {"path": path, "content": WIKI_PAGES[path]}, {"ETag": '"etag-1"'})
        return FakeResponse(404, {"message": "page not found"})

    def wiki_put(self, wid: str, params: dict, body: dict, **_: Any) -> dict:
        self.published[params.get("path")] = body.get("content", "")
        return {"path": params.get("path")}

    @staticmethod
    def test_cases(plan: str, suite: str, **_: Any) -> list[dict]:
        if suite == "3":
            return [{"workItem": {"id": 500 + i, "workItemFields": [
                {"Microsoft.VSTS.TCM.AutomationStatus": "Automated" if i < 6 else "Not Automated"}]}} for i in range(8)]
        if suite == "4":
            return [{"workItem": {"id": 600 + i, "workItemFields": []}} for i in range(5)]
        return []

    @staticmethod
    def packages(feed: str, **_: Any) -> list[dict]:
        if feed == "feed-1":
            return [
                {"id": "p1", "name": "Contoso.Platform.Core", "protocolType": "NuGet",
                 "versions": [{"version": "4.2.0", "publishDate": ago(10)}]},
                {"id": "p2", "name": "Contoso.Legacy.Utils", "protocolType": "NuGet",
                 "versions": [{"version": "1.0.3", "publishDate": ago(400)}]},
                {"id": "p3", "name": "@contoso/ui-kit", "protocolType": "npm",
                 "versions": [{"version": "2.8.1", "publishDate": ago(25)}]},
            ]
        return [{"id": "p9", "name": "Contoso.Shared.Logging", "protocolType": "NuGet",
                 "versions": [{"version": "1.1.0", "publishDate": ago(60)}]}]

    @staticmethod
    def memberships(descriptor: str, **_: Any) -> list[dict]:
        members = next((m for d, _, m in GROUPS if d == descriptor), [])
        return [{"memberDescriptor": m, "containerDescriptor": descriptor} for m in members]

    @staticmethod
    def identities(params: dict, **_: Any) -> list[dict]:
        names = {
            "Microsoft.TeamFoundation.Identity;S-1-9-contrib": ("[Platform]\\Contributors", True),
            "Microsoft.TeamFoundation.Identity;S-1-9-admins": ("[Platform]\\Project Administrators", True),
            "Microsoft.TeamFoundation.ServiceIdentity;build": ("Platform Build Service (contoso)", False),
        }
        out = []
        for d in params.get("descriptors", "").split(","):
            if d in names:
                out.append({"descriptor": d, "providerDisplayName": names[d][0], "isContainer": names[d][1]})
        return out

    @staticmethod
    def acls(namespace: str, params: dict, **_: Any) -> list[dict]:
        if namespace != "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87":
            return []
        root = f"repoV2/{PID}"
        return [
            {"token": root, "acesDictionary": {
                "Microsoft.TeamFoundation.Identity;S-1-9-contrib": {
                    "descriptor": "Microsoft.TeamFoundation.Identity;S-1-9-contrib",
                    "allow": _bits("GenericRead", "GenericContribute", "ForcePush", "CreateBranch", "PolicyExempt")},
                "Microsoft.TeamFoundation.Identity;S-1-9-admins": {
                    "descriptor": "Microsoft.TeamFoundation.Identity;S-1-9-admins", "allow": (1 << 16) - 1}}},
            {"token": f"{root}/repo-api", "acesDictionary": {
                "Microsoft.TeamFoundation.ServiceIdentity;build": {
                    "descriptor": "Microsoft.TeamFoundation.ServiceIdentity;build",
                    "allow": _bits("GenericRead", "GenericContribute", "ManagePermissions")}}},
        ]


class DemoAuth:
    def headers(self) -> dict[str, str]:
        return {}


def demo_client(**kw: Any):
    from azdo_analyser.core.client import AzureDevOpsClient

    return AzureDevOpsClient(f"https://dev.azure.com/{ORG}", DemoAuth(), session=DemoSession(), sleep=lambda _: None, **kw)
