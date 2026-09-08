# Azure demo: versioned, caller-authenticated prompt agent

This slice registers **one separate prompt agent**, `field-guide-coach`, using the
already deployed `gpt-5.4-mini`. It does not provision models, hosted compute,
containers, storage, search, gateways, agent applications, or scheduled inference.
It never writes to `triage-bot`, `deep-research-analyst`, or `foundry-concierge`.

The coach has **no tools or external actions**. Its instructions constrain factual
guidance to official source context supplied by the browser, require citations,
and explicitly distinguish a supplied snapshot from verified freshness or GA.
These are prompt-level behavior constraints, not a guarantee of answer quality or
proof that caller-supplied excerpts are authentic. The browser still needs to
provide meaningful official excerpts or summaries; a URL alone is not grounding.

## Recorded deployment status

At **2026-09-08T01:17:54Z**, `field-guide-coach` version **2** was active using
`gpt-5.4-mini`. This version recognizes the official repository owners and SDK
package sources used by the guide. The three original agents remained at version
**1**; the deployer never writes to them. A subsequent explicit deployment
returned **unchanged, version 2**, without creating another version.

The corrected, bounded deployment smoke **completed**, using 52 output tokens
and 836 total tokens. A separate console-shaped request with a 2000-token cap
also completed, and the project accepted the Responses POST CORS preflight from
the Pages origin. These establish basic connectivity, not an answer-quality gate.
An initial over-specified smoke request returned HTTP 400. The individual
offending option was not isolated; the smoke now uses the minimal, accepted
version-pinned request rather than adding redundant request-level overrides.

The GitHub **`azure-demo`** environment is prepared with a `main`-only branch
policy and the tenant/subscription variables below. The dedicated OIDC identity,
federation, project role assignment and `AZURE_CLIENT_ID` still require
repository-owner setup; no client secret is needed.

## Approved target and public site contract

| Setting | Value |
| --- | --- |
| Subscription | `65135929-9d4d-48c2-bea4-86a24070ea4c` |
| Tenant | `16b3c013-d300-468d-ac64-7eda0820b6d3` |
| Resource group | `fy26aifoundry` |
| Foundry resource / region | `swedenfoundry93` / `swedencentral` |
| Project | `foundry-showcase` |
| Project endpoint | `https://swedenfoundry93.services.ai.azure.com/api/projects/foundry-showcase` |
| Agent CRUD compatibility API | `2025-11-15-preview` |
| Responses API | `{project_endpoint}/openai/v1/responses` |
| Entra token scope | `https://ai.azure.com/.default` |

The immutable target is deliberate: this is not a general-purpose Azure provisioner.
Retargeting requires a reviewed change to both the config and the script's target
constants, not just a workflow input.

`demo/config.json` is public, contains no credentials, and has `schema_version: 1`.
Its required browser fields are `project_endpoint`, `agents_api_version`,
`resource_name`, `project_name`, `region`, `portal_url`, `preferred_agent`, and
`authentication_scope`. `gateway_endpoint` is null (only null or an empty string
is accepted). There is no public paid-inference proxy.

The portal URL uses the known public subscription, resource group, resource and
project IDs. Its first path component is the subscription UUID's 16 bytes encoded
as URL-safe base64 without padding: `ZRNZKZ1NSMK-pIaiQHDqTA`.

An optional `snapshot` is explicitly `kind: "point_in_time"` with a UTC
`last_verified` and an `agents` list of fetched `name`, `model`, and `version`
values. It is not live inventory, an availability promise, or a maturity claim.
Deploying never rewrites this committed snapshot or publishes Pages; refresh it
only after fetching the corresponding Azure data.

### Frontend wire contract at handoff

The current console sends `POST {project_endpoint}/openai/v1/responses` with
top-level `agent_reference: {type: "agent_reference", name, version}`, a string
`input`, and `max_output_tokens` defaulting to **2000** (the UI also offers 1000
and 4000). It adds `previous_response_id` only for a continuing conversation.
Response IDs must remain isolated by connection, agent and version.

The deployment smoke uses the same accepted project route, version-pinned
selector and minimal body shape, but a **160-token** cap. It does not override
the selected agent's stored tool or reasoning configuration.
`extra_body` is an SDK argument, not a REST JSON wrapper. Do not switch this
prompt-agent demo to hosted-agent endpoints or change its management API pin
solely because hosted-agent migration guidance recommends those changes.

## Local use

Python 3.12 and an authenticated Azure CLI are sufficient. The deployer and its
tests use only the Python standard library; no Foundry SDK or new package is
required. From PowerShell:

```powershell
Set-Location 'C:\Users\hammadaslam\OneDrive\Projects\Foundry-Demo-Site'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest discover -s tests -p 'test_deploy_demo.py'
python .\scripts\deploy_demo.py --check

# Only if not already authenticated in this tenant:
az login --tenant '16b3c013-d300-468d-ac64-7eda0820b6d3'

# Explicit mutation permission, but no inference:
python .\scripts\deploy_demo.py --deploy

# Optional: deploy/reconcile, then make exactly one short billable response:
python .\scripts\deploy_demo.py --deploy --smoke
```

`--check` validates all three JSON contracts **offline** and reports `configured`,
not `deployed`. JSON duplicate keys, non-finite values, wrong types, unknown fields,
oversized files, unsafe endpoints, other agent/model names, hosted definitions,
tools, proxies and misleading seed-evaluation claims fail closed. This is a
deliberately small supported subset of the documented prompt schema, not a copy of
every Foundry schema.

`--deploy` gets the existing agent and its `versions.latest.definition`, then
compares the manifest's definition, description and metadata. Object key order,
read-only response fields, omitted empty tools, null optional definition fields,
and documented default sampling values do not create unnecessary versions.
Non-default behavior changes do. A different, unowned same-name agent is not
overwritten. Disabled, draft, non-prompt or non-active versions require inspection.

When a version is needed the script calls the explicit **create-version** operation:

```http
POST {project_endpoint}/agents/field-guide-coach/versions?api-version=2025-11-15-preview
Content-Type: application/json
```

The body contains only `description`, `metadata`, and `definition` from
`demo/agent.json`; local `schema_version` and `name` are not sent as body fields.
The returned version is read back with a version-specific GET before success is
reported. An unchanged agent does not receive a POST.

**No POST is automatically retried**, including after 429, timeout, service error,
malformed JSON or failed readback. A failed request may have created a version or
incurred inference charges. Inspect Foundry first. A later explicit deployment
starts by reading the current definition again, so a previously completed write
normally reconciles as unchanged. Serialize local operators as well as Actions;
there is no cross-machine lock or claim of atomic compare-and-create.

### Authentication and error handling

The script captures `az account get-access-token` stdout/stderr in process memory,
requests only the `https://ai.azure.com/.default` scope, pins the subscription,
and checks the returned tenant, subscription and expiry. Azure CLI does not
accept `--tenant` together with `--subscription` for this command, so the tenant
is validated in the captured result rather than passed as a conflicting flag.
It does not print tokens,
put them in command arguments or environment exports, create token files, save
responses, or include raw CLI/HTTP error bodies in errors or workflow artifacts.
Azure CLI manages its own existing sign-in cache; this script does not implement
or export an authentication cache. Do not run it under HTTP/token debug logging.

Requests go only to the exact approved HTTPS project and allowlisted operations.
All HTTP redirects, even same-origin redirects, are refused. The CLI timeout is
45 seconds, ordinary HTTP connect/read timeout 30 seconds, smoke timeout 60
seconds, and HTTP bodies are capped at 2 MiB. Workflow execution also has a
10-minute job limit. Errors identify the failing phase and give sanitized
authentication, RBAC, quota, network or contract guidance without echoing bodies.

### One smoke response is not an evaluation

`--smoke` targets the verified immutable version using:

```json
{
  "agent_reference": {
    "type": "agent_reference",
    "name": "field-guide-coach",
    "version": "2"
  }
}
```

The full body contains only `agent_reference`, `input` and `max_output_tokens`.
It supplies a short official-source excerpt, asks one question, and caps output
at **160 tokens**. The stored agent definition has no tools and uses reasoning
effort `none`; the request does not resend these options. There is no follow-up
conversation, polling, or retry. No retention override is requested: Foundry's
default response storage and service safety/operational policies apply. Use
only non-sensitive inputs.

Success requires response `status: "completed"`, no error/incomplete details,
and nonempty completed assistant text in the structured output. HTTP 200 alone,
an unfinished response, an empty response, a tool call or a refusal is not smoke
success. Only status, output character count and available token counts are
printed, not model output. If deployment succeeded but smoke failed, the command
exits nonzero while still reporting the deployed version and failed smoke.

## GitHub Actions setup: OIDC, not client secrets

The repository contains a **manual-only** workflow,
`.github/workflows/deploy-azure-demo.yml`. Adding it does not configure an identity
or grant Azure permissions. Complete the remaining identity setup before
dispatching it.

1. Create or choose a dedicated Microsoft Entra application/service principal in
   tenant `16b3c013-d300-468d-ac64-7eda0820b6d3`. Do not create a client secret and do
   not use `az ad sp create-for-rbac` to generate a password. Record its Application
   (client) ID and the service principal's object ID; these are different IDs.
2. On that application, add a GitHub Actions **environment** federated credential
   with the values below. Do not substitute a branch, pull-request, wildcard or
   another environment subject.
3. Assign the service principal the verified developer role at the exact project
   scope below. No subscription Owner/Contributor, model provisioning role,
   deployment-resource creation or role-assignment permission is needed by the job.
4. Review the prepared GitHub environment **`azure-demo`**. Its deployment branch
   policy already permits only `main`, not tags. Add required reviewers
   where available. These environment protections are essential: an
   environment-scoped OIDC subject alone does not restrict a branch whose workflow
   file was edited. Protect the default branch and review deployment-file changes.
5. Set **`AZURE_CLIENT_ID`** on that environment. `AZURE_TENANT_ID` and
   `AZURE_SUBSCRIPTION_ID` are already configured with the values below.
   Environment secrets with the same names are supported as a fallback, but
   these identifiers are not client secrets. Never configure an
   `AZURE_CLIENT_SECRET` or an Azure credentials JSON blob.

| Federated credential field | Exact value |
| --- | --- |
| Suggested name | `foundry-demo-github-azure-demo` |
| Issuer | `https://token.actions.githubusercontent.com` |
| Subject | `repo:haslam93/Foundry-Demo-Site:environment:azure-demo` |
| Audience | `api://AzureADTokenExchange` |

| GitHub environment variable | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | Application (client) GUID of the dedicated federated identity |
| `AZURE_TENANT_ID` | `16b3c013-d300-468d-ac64-7eda0820b6d3` |
| `AZURE_SUBSCRIPTION_ID` | `65135929-9d4d-48c2-bea4-86a24070ea4c` |

### Verified least-privilege built-in roles

Role definitions were fetched read-only from this subscription on 2026-09-08 UTC.
Use the role GUID, not an old display name:

| Caller | Role and scope | Important boundary |
| --- | --- | --- |
| Deployment identity | **Foundry User**, `53ca6127-db72-4b80-b1b0-d745d6d5456d`, at the project scope | The narrowest documented built-in developer role for agent creation/readback and testing; still broader than a single operation. Its data actions include `Microsoft.CognitiveServices/*`, with exclusions. Project scoping matters. |
| Invoke-only consumer | **Foundry Agent Consumer**, `eed3b665-ab3a-47b6-8f48-c9382fb1dad6`, at project or individual agent scope | Its sole verified data action is `Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action`; it does not grant the hub's agent-listing operation. |

The current browser performs live agent discovery plus Responses requests with
the **caller's Entra token and caller RBAC**. Foundry User at project scope supports
that developer demo. Do not grant it to anonymous site visitors. An invoke-only
consumer experience would need a client that already knows its permitted
agent/version instead of using developer agent discovery; this slice does not
change that browser behavior or create a proxy.

The exact project scope is:

```text
/subscriptions/65135929-9d4d-48c2-bea4-86a24070ea4c/resourceGroups/fy26aifoundry/providers/Microsoft.CognitiveServices/accounts/swedenfoundry93/projects/foundry-showcase
```

An administrator with existing role-assignment authority can assign the deploy
role in PowerShell (replace only the principal object ID):

```powershell
$projectScope = '/subscriptions/65135929-9d4d-48c2-bea4-86a24070ea4c/resourceGroups/fy26aifoundry/providers/Microsoft.CognitiveServices/accounts/swedenfoundry93/projects/foundry-showcase'
az role definition list --name '53ca6127-db72-4b80-b1b0-d745d6d5456d' --query '[].{role:roleName,permissions:permissions}' --output json
az role assignment create --assignee-object-id '<SERVICE-PRINCIPAL-OBJECT-ID>' --assignee-principal-type ServicePrincipal --role '53ca6127-db72-4b80-b1b0-d745d6d5456d' --scope $projectScope
```

No roles, application registrations or federated credentials are created by the
deployer. Allow RBAC propagation, then use **Actions > Deploy Azure demo prompt
agent > Run workflow**, choose `main`, and leave `smoke` **false** unless one paid
response is intended. The equivalent manual GitHub CLI path is:

```powershell
gh workflow run deploy-azure-demo.yml --repo haslam93/Foundry-Demo-Site --ref main -f smoke=false
```

The job uses immutable action commit pins, Python 3.12, the repository's existing
pinned requirements, `contents: read`, and `id-token: write`. It validates the
repository/default branch, manifest, all three identity GUIDs and the boolean
smoke input **before Azure login**. Concurrent dispatches are serialized and never
cancel an in-flight create. Invalid branch dispatches report blocked without
logging into Azure. No scheduled or push-triggered deployment/inference is added.

The summary distinguishes **configured** (offline validation only), **blocked**
(missing IDs, auth/RBAC failure, or unverified write), and **deployed** (created
or already-equivalent version read from Azure). A timeout after POST is explicitly
unverified, not proof that nothing changed. A failed optional smoke does not
pretend deployment was rolled back.

## Costs, rollback and evaluation boundaries

Prompt-agent registration adds no hosted runtime or continuous compute in this
slice. The existing Azure model/resource retains its existing billing terms.
Every browser request and optional smoke can incur model charges, including
requests whose client times out. A 160-token output cap limits smoke generation,
not total input tokens or a dollar budget. Nothing here promises free inference.

Rollback is **version selection, not deletion**: retain a previously known-good
version and select it in the Foundry playground or set the caller's
`agent_reference.version` explicitly. Agent names without an explicit version,
and a browser that rediscovers only `versions.latest`, still resolve the latest
version; editing the public snapshot does not roll that back. Do not delete
existing versions, other agents or resource infrastructure. If a default caller
needs a rollback, its owner must deliberately pin the desired version.

`demo/evals.json` contains eight hand-authored **manual seed scenarios** with
queries, context and concrete expected-behavior rubrics. They cover grounding,
missing context, maturity/freshness, RBAC, injection, source spoofing, external
actions and misleading evaluation claims. `--check` and unit tests validate their
local shape, not answer quality. No dataset is uploaded, no evaluator or Foundry
suite is registered, and no automated quality gate or continuous evaluation is
claimed. Managed evaluations and tracing require a separately approved setup;
see the official [observability and evaluation guidance][observability].

## Protocol references

The agent API is intentionally pinned to the already reachable
`2025-11-15-preview` compatibility contract, not automatically advanced to the
current reference's `v1`. This pin is not a claim that it is the newest or GA API.
The explicit create-version operation and prompt fields are documented in
[Foundry's REST reference][create-version] and [Python SDK operations][operations].
Current API-reference pages can evolve; keep the compatibility pin under review
and exercise these contract tests before deliberately changing it.

- [Get an agent and its `versions.latest` definition][get-agent]
- [Create a response: `agent_reference`, `/openai/v1/responses`][responses]
- [Project client endpoint and Entra scope][project-client]
- [Prompt definition schema][prompt-definition]
- [Current prompt-agent quickstart][prompt-quickstart]
- [Foundry roles, project scope and consumer limitations][rbac]
- [Azure Login with GitHub OIDC][oidc]

[create-version]: https://ai.azure.com/api-reference/llm/agent-versions/create-agent-version-agents-create-agent-version-from-code.md
[get-agent]: https://ai.azure.com/api-reference/llm/agents/get-agent.md
[responses]: https://ai.azure.com/api-reference/llm/responses/create-response.md
[operations]: https://learn.microsoft.com/python/api/azure-ai-projects/azure.ai.projects.operations.agentsoperations?view=azure-python-preview
[project-client]: https://learn.microsoft.com/python/api/azure-ai-projects/azure.ai.projects.aiprojectclient
[prompt-definition]: https://learn.microsoft.com/python/api/azure-ai-projects/azure.ai.projects.models.promptagentdefinition?view=azure-python-preview
[prompt-quickstart]: https://learn.microsoft.com/azure/foundry/agents/quickstarts/prompt-agent
[rbac]: https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry
[oidc]: https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect
[observability]: https://learn.microsoft.com/azure/foundry/concepts/observability
