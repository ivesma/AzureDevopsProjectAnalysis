# Permissions

The tool only reads, except when `wiki_publish` is enabled. Run it with an identity that is a member of
**Project Administrators** (or **Project Collection Administrators** for organisation-level data such as audit
log, user entitlements and agent pools) for the most complete result. Anything that cannot be read is listed in
the *Coverage* sheet / *Data Coverage* page; the rest of the assessment is still produced.

## Authentication options

| Method | How | Notes |
|---|---|---|
| PAT (`--auth pat`, default) | `--pat`, `pat:` in config, or `AZDO_PAT` / `AZURE_DEVOPS_EXT_PAT` / `SYSTEM_ACCESSTOKEN` | Prefer the environment variable so the token is not in shell history |
| Azure CLI (`--auth azcli`) | `az login` first | Uses `az account get-access-token` for the Azure DevOps resource; token refreshed automatically |
| Managed identity (`--auth managed_identity`) | Run on an Azure VM / container with an identity added to the organisation | Preview: uses the instance metadata service |

Inside a pipeline you can pass `$(System.AccessToken)` as `SYSTEM_ACCESSTOKEN`; the build service identity
usually lacks security, audit and entitlement read access, so expect coverage gaps.

## PAT scopes

| Area | Scope |
|---|---|
| Project, teams | Project and Team — Read |
| Repositories, policies, commits, PRs, YAML | Code — Read |
| Pipelines, runs, task catalogue | Build — Read |
| Classic releases, deployments | Release — Read |
| Environments, checks, pipeline permissions | Environment — Read & manage *(checks/permissions APIs require manage-level access)* |
| Agent pools, queues, agents | Agent Pools — Read |
| Variable groups | Variable Groups — Read |
| Secure files | Secure Files — Read |
| Service connections, execution history | Service Connections — Read & query |
| Work items, types, backlog config | Work Items — Read |
| Wiki | Wiki — Read (Read & write for `--wiki-publish`) |
| Test plans, runs | Test Management — Read |
| Feeds, packages | Packaging — Read |
| Groups, memberships, identities | Graph — Read, Identity — Read |
| ACLs / security namespaces | Security — Manage *(reading ACLs requires this scope)* |
| User access levels | Member Entitlement Management — Read |
| PAT & policy audit events | Read Audit Log |

If in doubt, a full-access PAT on a dedicated **read-only** account with Project Administrator membership is the
simplest way to get complete coverage.

## Known limits

- **Service connection secret expiry** is not exposed by Azure DevOps. Secret-based Azure RM connections are
  flagged so they can be moved to workload identity federation; check expiry in Entra ID.
- **External repositories** (GitHub, Bitbucket) hosting pipeline YAML are not read; their pipelines appear with
  "YAML not retrieved". Template repositories on Azure Repos in *other projects* are resolved only when the
  identity can read them.
- **PAT usage** is derived from audit events (creation/update/revocation); listing individual users' PATs
  requires PAT admin APIs and is out of scope.
