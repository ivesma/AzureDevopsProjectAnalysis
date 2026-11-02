# Risk rule catalogue

Severity weights: CRITICAL 25 · HIGH 10 · MEDIUM 4 · LOW 1 · INFO 0. Production resources are detected with
`production_pattern` (default: `prod`, `production`, `prd`, `live`, CamelCase aware — `DeployProd` matches).

## Source control

| Rule | Severity | Finding |
|---|---|---|
| SC001 | HIGH | Default branch has no branch policies |
| SC002 | MEDIUM | No minimum reviewer policy |
| SC003 | MEDIUM | Authors can approve their own pull requests (1 reviewer + creator vote counts) |
| SC004 | MEDIUM | No build validation on pull requests |
| SC005 | MEDIUM | All branch policies are optional (non-blocking) |
| SC010 | LOW | Stale repository (no commits for `stale_days`) |
| SC011 | LOW | Empty repository |

## Deployment

| Rule | Severity | Finding |
|---|---|---|
| DP001 | HIGH | Production environment has no approvals or checks |
| DP002 | MEDIUM | Production environment has checks but no human approval |
| DP003 | MEDIUM | Requester can approve their own production deployment |
| DP004 | MEDIUM | Production environment open to all pipelines |
| DP010 | HIGH | Classic release: production stage without pre-deployment approval |
| DP011 | MEDIUM | Classic release: release creator can approve production |
| DP012 | LOW | Classic release: no deployment gates on production |
| DP020 | HIGH | **Direct deployment to production**: a regular (non-deployment) YAML job in a production stage deploys through a service connection, bypassing environment checks |

## Resources

| Rule | Severity | Finding |
|---|---|---|
| RS001 | MEDIUM | Unused service connection (no references, no execution history) |
| RS002 | LOW | Service connection not used for `stale_days` |
| RS003 | MEDIUM/HIGH | Service connection granted to all pipelines (HIGH for production) |
| RS004 | MEDIUM | Azure RM connection uses a client secret — convert to workload identity federation |
| RS005 | MEDIUM/HIGH | Service connection credential expiring / expired (when exposed) |
| RS006 | MEDIUM | Service connection not ready |
| RS007 | MEDIUM | Production service connection without approvals/checks |
| RS010 | LOW | Unused variable group |
| RS011 | MEDIUM | Secret variable group open to all pipelines |
| RS012 | LOW | Secrets stored in Azure DevOps instead of Key Vault |
| RS013 | MEDIUM | Secure file open to all pipelines |
| RS020 | MEDIUM | Self-hosted pool with no agents |
| RS021 | LOW | Offline agents |
| RS022 | INFO | Disabled agents |
| RS023 | LOW | Mixed agent versions |
| RS024 | MEDIUM | Self-hosted pool open to all pipelines |
| RS030 | INFO | Environment never deployed to |

## Pipelines and YAML anti-patterns

| Rule | Severity | Finding |
|---|---|---|
| PL001 | INFO | Disabled pipeline |
| PL002 | LOW | Pipeline not run in the analysis window |
| PL003 | MEDIUM | Build success rate below `min_build_success_rate` (≥ 3 runs) |
| PL004 | LOW | Classic (designer) build pipeline |
| PL005 | LOW | Secrets defined directly on the pipeline |
| PL006 | LOW | Classic release pipeline |
| PL007 | INFO | Pipeline-like YAML not registered as a pipeline or used as a template |
| PL020 | LOW | Naming inconsistency (pipelines, repositories, service connections) |
| AP001 | HIGH | Plain-text secret literal in YAML |
| AP002 | MEDIUM | Deprecated Microsoft-hosted image |
| AP003 | LOW | Task without `@major` version |
| AP004 | LOW | `continueOnError` masks failures |
| AP005 | MEDIUM | `persistCredentials: true` on checkout |
| AP006 | MEDIUM | Remote script piped to a shell (`curl … \| bash`, `iex DownloadString`) |
| AP007 | MEDIUM | Cross-repository template consumed from a branch instead of a tag |
| AP008 | LOW | Implicit CI trigger (no `trigger:`) |
| AP011 | INFO | Floating `*-latest` image |
| AP012 | LOW | Inline script longer than 50 lines |
| AP013 | INFO | `System.AccessToken` used in scripts |
| AP014 | MEDIUM | YAML parse error |
| AP015 | LOW | Deprecated task version |
| AP016 | INFO | Template could not be resolved |
| AP020 | LOW | More than 30 inline variables |
| AP021 | LOW | Template nesting deeper than 5 |

## Security

| Rule | Severity | Finding |
|---|---|---|
| SE001 | MEDIUM | More direct Project Administrators than `max_project_admins` |
| SE002 | HIGH | Broad group (Contributors, Readers, Valid Users, Everyone) explicitly granted dangerous permissions (force push, bypass policies, manage permissions, edit policies, delete …) |
| SE003 | HIGH | Build service identity holds elevated permissions |
| SE010 | LOW | User inactive for 90+ days (non-Stakeholder) |
| SE020 | INFO/MEDIUM | PAT creation events in the audit window |
| AR001 | LOW | Feed with packages mixes public upstreams (dependency confusion risk) |

## Boards, documentation, testing

| Rule | Severity | Finding |
|---|---|---|
| BO001 | LOW | More than 40% of open work items unassigned |
| BO002 | LOW | More than 10 open items older than `stale_days` |
| DO001 | LOW | Broken wiki links |
| DO002 | LOW | Orphaned wiki pages (no inbound links, no views in 30 days) |
| TE001 | LOW | Test automation coverage below 50% |
