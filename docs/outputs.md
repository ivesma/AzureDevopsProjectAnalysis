# Output reference

```
assessment/
  Excel/ProjectAssessment.xlsx
  HTML/index.html
  Wiki/Project-Assessment.md
  Wiki/Project-Assessment/{Executive-Summary,Repositories,Pipelines,...}.md
  Wiki/Project-Assessment/Pipelines/<pipeline>.md
  Markdown/README.md, INDEX.md, <section>.md, Pipelines/<pipeline>.md ...
  Mermaid/pipelines/stage-flow-<id>.mmd, release-flow-<id>.mmd
  Mermaid/dependencies/{pipeline-dependencies,template-usage,repository-dependencies,
                        service-connections,variable-groups,agent-pools}.mmd
  Mermaid/environments/environments.mmd
  Mermaid/boards/work-item-hierarchy.mmd
  Mermaid/wiki/wiki-sitemap-<wiki>.mmd
  Mermaid/security/security-groups.mmd
  DrawIO/<category>/*.drawio
  Graphviz/<category>/*.dot
  Json/full-export.json
  History/run-<timestamp>.json
  PowerBI/*.csv, model.json            (with --powerbi)
```

## Excel sheets

| Sheet | Contents |
|---|---|
| Index | links to every sheet |
| Summary | project metadata, scores, finding counts, inventory |
| Repositories | inventory, activity, branch policies, technologies |
| Pipelines | definition, triggers, variables, templates, service connections, pools, environments, complexity, runs |
| PipelineDependencies | source → target relations (triggers, pipeline resources, artifacts, PR validation) |
| Templates | template files, usage level, consuming pipelines |
| Variables | every variable by owner, secret flag, source |
| VariableGroups | groups, Key Vault link, secrets, open access, consumers |
| VariableMatrix | consumer × variable group matrix |
| ServiceConnections | type, auth scheme, checks, open access, last use, consumers, unused flag |
| ServiceConnectionUsage | consumer rows with evidence (YAML, classic task, execution history, k8s resource, Key Vault link) |
| Environments | resources, checks, approvers, deployments |
| ReleasePipelines | one row per classic release stage: approvals, gates, tasks, connections |
| AgentPools / Agents | pools, usage, agents, versions, capabilities |
| WorkItems | work items with lead and cycle time |
| Wiki | pages, views, last author, links, broken links, orphans |
| TestPlans | suites with automation coverage |
| Feeds | packages, versions, age, upstreams, consumers |
| SecureFiles | secure files and authorisations |
| Permissions / GroupMembers | explicit ACL entries and group membership |
| SecurityFindings | security findings |
| RiskRegister | all findings with Owner / Status / Target Date columns for tracking |
| AntiPatterns | YAML anti-patterns |
| Metrics | activity, boards, tests, cost, technical debt and risk-by-category metrics (long format) |
| Recommendations | prioritised, grouped recommendations with remediation steps |
| Coverage | areas not collected and why |

Every data sheet is an Excel table (filters, banding, structured references usable directly as a PivotTable
source). Severity columns use conditional fills; scores use colour scales; rates use data bars; URL columns are
hyperlinks.

## Mermaid flavours

`.mmd` files and the Markdown docs use standard Mermaid (`graph` syntax for compatibility). Wiki pages use the
Azure DevOps `::: mermaid` block. Large graphs are truncated (150–200 nodes) with an explicit "… more" node.
