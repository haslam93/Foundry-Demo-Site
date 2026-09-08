"""Deploy only the tool-free demo prompt agent; see docs/azure-demo.md for REST references."""

import argparse
import base64
import copy
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION_ID = "65135929-9d4d-48c2-bea4-86a24070ea4c"
TENANT_ID = "16b3c013-d300-468d-ac64-7eda0820b6d3"
RESOURCE_GROUP = "fy26aifoundry"
RESOURCE_NAME = "swedenfoundry93"
PROJECT_NAME = "foundry-showcase"
REGION = "swedencentral"
REPOSITORY = "haslam93/Foundry-Demo-Site"
AGENT_NAME = "field-guide-coach"
MODEL_NAME = "gpt-5.4-mini"
API_VERSION = "2025-11-15-preview"
AUTH_SCOPE = "https://ai.azure.com/.default"
PROJECT_ENDPOINT = f"https://{RESOURCE_NAME}.services.ai.azure.com/api/projects/{PROJECT_NAME}"
SUBSCRIPTION_KEY = base64.urlsafe_b64encode(uuid.UUID(SUBSCRIPTION_ID).bytes).rstrip(b"=").decode()
PORTAL_URL = (
    f"https://ai.azure.com/nextgen/r/{SUBSCRIPTION_KEY},{RESOURCE_GROUP},,"
    f"{RESOURCE_NAME},{PROJECT_NAME}/build/agents"
)
AGENT_ROUTE = f"/agents/{AGENT_NAME}"
METADATA = {"managed_by": REPOSITORY, "manifest_schema": "1"}
CLI_TIMEOUT = 45
REQUEST_TIMEOUT = 30
SMOKE_TIMEOUT = 60
SMOKE_MAX_OUTPUT_TOKENS = 160
MAX_FILE_BYTES = 65_536
MAX_RESPONSE_BYTES = 2_097_152
VERSION_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
SMOKE_INPUT = (
    "Official source context supplied by the caller (not independently fetched by you):\n"
    "[S1] https://ai.azure.com/api-reference/llm/agent-versions/"
    "create-agent-version-agents-create-agent-version-from-code.md\n"
    "Excerpt: Creates a new version for the specified agent and returns the created version resource.\n"
    "Question: In one sentence under 35 words, explain what creating an agent version does. "
    "Cite [S1]; make no current availability or GA claim."
)


class DemoError(Exception):
    """An actionable error safe to display without server bodies or credentials."""

    def __init__(self, message, *, mutation_possible=False):
        super().__init__(message)
        self.mutation_possible = mutation_possible


def require(condition, message):
    if not condition:
        raise DemoError(message)


def object_fields(value, required, optional=(), label="Contract"):
    require(isinstance(value, dict), f"{label} must be a JSON object.")
    require(set(required) <= value.keys() <= set(required) | set(optional),
            f"{label} has missing or unsupported fields; see docs/azure-demo.md.")


def nonempty_text(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def version_string(value):
    require(isinstance(value, str) and re.fullmatch(VERSION_PATTERN, value) is not None,
            "Azure returned an invalid agent version identifier; inspect the agent in Foundry.")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON contains duplicate object keys.")
        result[key] = value
    return result


def reject_constant(_value):
    raise DemoError("Non-finite numbers are not allowed in JSON contracts.")


def parse_json(data):
    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise DemoError("Invalid JSON; raw content is withheld.") from None


def load_json(path):
    try:
        require(not path.is_symlink(), "Contract files must not be symbolic links.")
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise DemoError("Cannot read a demo contract file; check the three demo JSON files.") from None
    require(len(data) <= MAX_FILE_BYTES, "A demo contract file exceeds the 64 KiB limit.")
    return parse_json(data)


def validate_config(config):
    expected = {
        "schema_version": 1,
        "project_endpoint": PROJECT_ENDPOINT,
        "agents_api_version": API_VERSION,
        "resource_name": RESOURCE_NAME,
        "project_name": PROJECT_NAME,
        "region": REGION,
        "portal_url": PORTAL_URL,
        "preferred_agent": AGENT_NAME,
        "authentication_scope": AUTH_SCOPE,
    }
    object_fields(config, expected, ("gateway_endpoint", "snapshot"), "Demo configuration")
    require(type(config["schema_version"]) is int, "Configuration schema_version must be integer 1.")
    for field, value in expected.items():
        require(config[field] == value,
                f"Configuration {field} does not match this demo's approved Azure target.")
    require(config.get("gateway_endpoint") in (None, ""), "Public inference gateways are not supported.")
    if "snapshot" in config:
        snapshot = config["snapshot"]
        object_fields(snapshot, ("kind", "last_verified", "agents"), label="Deployment snapshot")
        require(snapshot["kind"] == "point_in_time", "Snapshot must be labeled point_in_time.")
        try:
            datetime.strptime(snapshot["last_verified"], "%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            raise DemoError("Snapshot last_verified must be a UTC timestamp ending in Z.") from None
        require(isinstance(snapshot["agents"], list) and 0 < len(snapshot["agents"]) <= 100,
                "Snapshot must contain a bounded, nonempty agent list.")
        seen = set()
        for agent in snapshot["agents"]:
            object_fields(agent, ("name", "model", "version"), label="Snapshot agent")
            require(nonempty_text(agent["name"], 63) and
                    re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", agent["name"]) is not None,
                    "Snapshot agent name is invalid.")
            require(agent["name"] not in seen, "Snapshot contains duplicate agents.")
            require(nonempty_text(agent["model"], 100), "Snapshot model is invalid.")
            version_string(agent["version"])
            seen.add(agent["name"])


def validate_manifest(manifest):
    object_fields(manifest, ("schema_version", "name", "description", "metadata", "definition"),
                  label="Agent manifest")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1,
            "Agent manifest schema_version must be integer 1.")
    require(manifest["name"] == AGENT_NAME, "Only field-guide-coach may be deployed by this script.")
    require(nonempty_text(manifest["description"], 1_024), "Agent description is missing or too long.")
    require(manifest["metadata"] == METADATA, "Agent ownership metadata does not match this repository.")
    definition = manifest["definition"]
    object_fields(definition, ("kind", "model", "instructions", "tools", "tool_choice", "reasoning"),
                  label="Prompt definition")
    require(definition["kind"] == "prompt", "Only a prompt agent is supported; no hosted provisioning.")
    require(definition["model"] == MODEL_NAME, "Use only the existing gpt-5.4-mini deployment.")
    require(nonempty_text(definition["instructions"], 20_000), "Agent instructions are missing or too long.")
    require(definition["tools"] == [] and definition["tool_choice"] == "none",
            "The demo agent must have no tools and tool_choice none.")
    require(definition["reasoning"] == {"effort": "none"}, "This low-cost demo requires reasoning effort none.")


def validate_evals(evals):
    object_fields(evals, ("schema_version", "agent", "stage", "version", "execution",
                         "registered_in_foundry", "scenarios"), label="Seed scenarios")
    require(type(evals["schema_version"]) is int and evals["schema_version"] == 1,
            "Seed schema_version must be integer 1.")
    require(evals["agent"] == AGENT_NAME and evals["stage"] == "seed" and evals["version"] == "v1",
            "Seed scenarios must target field-guide-coach, stage seed, version v1.")
    require(evals["execution"] == "manual-only" and evals["registered_in_foundry"] is False,
            "Local seeds must not claim automated execution or Foundry registration.")
    require(isinstance(evals["scenarios"], list) and 1 <= len(evals["scenarios"]) <= 20,
            "Provide between 1 and 20 manual seed scenarios.")
    ids, queries = set(), set()
    for row in evals["scenarios"]:
        object_fields(row, ("id", "query", "context", "expected_behavior"), label="Seed scenario")
        require(all(nonempty_text(row[key], 6_000) for key in row), "Seed scenario fields must be nonempty text.")
        require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["id"]) is not None, "Seed scenario id is invalid.")
        require(row["id"] not in ids and row["query"] not in queries, "Seed scenarios must be unique.")
        ids.add(row["id"])
        queries.add(row["query"])


def load_contracts(root=ROOT):
    config = load_json(root / "demo" / "config.json")
    manifest = load_json(root / "demo" / "agent.json")
    evals = load_json(root / "demo" / "evals.json")
    validate_config(config)
    validate_manifest(manifest)
    validate_evals(evals)
    return config, manifest, evals


def validate_ci_environment(env):
    require(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPOSITORY,
            "CI deployment is restricted to haslam93/Foundry-Demo-Site on GitHub Actions.")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "CI deployment must be manually dispatched.")
    branch = env.get("DEMO_DEFAULT_BRANCH", "")
    require(isinstance(branch, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) is not None and
            env.get("GITHUB_REF_TYPE") == "branch" and env.get("GITHUB_REF") == f"refs/heads/{branch}",
            "CI deployment is restricted to the repository's default branch.")
    require(env.get("DEMO_ENVIRONMENT") == "azure-demo", "CI must use the azure-demo environment.")
    for field in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"):
        value = env.get(field, "")
        require(isinstance(value, str) and
                re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) is not None
                and uuid.UUID(value).int != 0,
                f"Set {field} to a nonzero GUID in the azure-demo environment variables (or equivalent secrets).")
    require(env["AZURE_TENANT_ID"].lower() == TENANT_ID and
            env["AZURE_SUBSCRIPTION_ID"].lower() == SUBSCRIPTION_ID,
            "CI tenant/subscription IDs do not match the approved demo target.")
    require(not env.get("AZURE_CLIENT_SECRET"), "Client secrets are unsupported; configure OIDC federation.")
    require(env.get("DEMO_SMOKE") in ("true", "false"), "The smoke input must be exactly true or false.")


def get_access_token():
    az = shutil.which("az")
    require(az is not None, "Azure CLI is missing. Install it, then sign in to the documented tenant.")
    env = dict(os.environ, AZURE_CORE_COLLECT_TELEMETRY="false", AZURE_CORE_LOGGING_ENABLE_LOG_FILE="false")
    try:
        # Azure CLI rejects --tenant with --subscription; verify the returned tenant below.
        reply = subprocess.run(
            [az, "account", "get-access-token", "--subscription", SUBSCRIPTION_ID,
             "--scope", AUTH_SCOPE, "--output", "json", "--only-show-errors"],
            capture_output=True, timeout=CLI_TIMEOUT, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DemoError("Azure CLI token acquisition failed or timed out. Sign in again; CLI output is withheld.") from None
    require(reply.returncode == 0, "Azure CLI authentication failed. Check login/OIDC and project access; CLI output is withheld.")
    require(len(reply.stdout) <= MAX_FILE_BYTES, "Azure CLI returned an oversized authentication response.")
    info = parse_json(reply.stdout)
    require(isinstance(info, dict), "Azure CLI returned invalid authentication metadata.")
    require(isinstance(info.get("tenant"), str) and isinstance(info.get("subscription"), str) and
            info["tenant"].lower() == TENANT_ID and info["subscription"].lower() == SUBSCRIPTION_ID,
            "Azure CLI returned a token for the wrong tenant or subscription.")
    token = info.get("accessToken")
    require(isinstance(token, str) and 0 < len(token) <= 16_384 and
            re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is not None,
            "Azure CLI returned an invalid access token; token content is withheld.")
    expiry = info.get("expires_on")
    require(type(expiry) is int or isinstance(expiry, str) and
            1 <= len(expiry) <= 12 and expiry.isascii() and expiry.isdigit(),
            "Azure CLI did not return a valid expires_on timestamp; update the CLI and sign in again.")
    require(int(expiry) > time.time() + 30, "Azure CLI returned an expired or nearly expired token. Sign in again.")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an Authorization header, including to another Azure host.
        return None


class AzureHTTPError(DemoError):
    def __init__(self, status, method):
        self.status = status
        help_text = {
            400: "Check the pinned API, prompt manifest and existing model deployment.",
            401: "Refresh the Entra login/OIDC token with the ai.azure.com scope.",
            403: "Check project-scoped Foundry User RBAC and network access; do not weaken network restrictions.",
            404: "Check the existing project, agent/version and model; this script does not provision missing resources.",
            409: "Inspect the latest agent version for a conflicting deployment before running again.",
            429: "Check Azure quota/rate limits and wait before an explicitly approved retry.",
        }.get(status, "Inspect the Foundry project and Azure service health.")
        if 300 <= status < 400:
            help_text = "An HTTP redirect was refused; no token was forwarded to its destination."
        super().__init__(f"Azure HTTP {status}. {help_text}" + mutation_warning(method),
                         mutation_possible=method == "POST")


def mutation_warning(method):
    if method == "POST":
        return " POST was not retried; a version or paid response may already exist. Inspect Foundry before rerunning."
    return ""


class AzureClient:
    def __init__(self, config, token, opener=None):
        validate_config(config)
        self.endpoint = config["project_endpoint"]
        self._token = token
        self._opener = opener if opener is not None else urllib.request.build_opener(NoRedirect())

    def clear_token(self):
        self._token = None

    def request(self, method, route, payload=None, timeout=REQUEST_TIMEOUT):
        read_route = route == AGENT_ROUTE or re.fullmatch(
            re.escape(AGENT_ROUTE) + r"/versions/" + VERSION_PATTERN, route
        ) is not None
        write_route = route in (AGENT_ROUTE + "/versions", "/openai/v1/responses")
        require((method == "GET" and read_route and payload is None) or
                (method == "POST" and write_route and isinstance(payload, dict)),
                "Request refused: only this agent's reads, version creation and smoke response are allowed.")
        require(type(timeout) is int and 1 <= timeout <= SMOKE_TIMEOUT, "Request timeout is outside the allowed bound.")
        require(self._token is not None, "The Azure authentication session has already been cleared.")
        url = self.endpoint + route
        if route.startswith("/agents/"):
            url += "?api-version=" + API_VERSION
        body = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={"Authorization": "Bearer " + self._token, "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "foundry-demo-manifest/1"},
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if response.status not in (200, 201):
                    raise AzureHTTPError(response.status, method)
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise AzureHTTPError(status, method) from None
        except (OSError, http.client.HTTPException):
            raise DemoError("Azure network/TLS request failed or timed out. Check connectivity." +
                            mutation_warning(method)) from None
        require(len(data) <= MAX_RESPONSE_BYTES, "Azure response exceeded the size limit." + mutation_warning(method))
        try:
            result = parse_json(data)
        except DemoError:
            raise DemoError("Azure returned invalid JSON; raw response is withheld." + mutation_warning(method)) from None
        require(isinstance(result, dict), "Azure returned an unexpected response shape." + mutation_warning(method))
        return result


def version_payload(manifest):
    validate_manifest(manifest)
    return copy.deepcopy({key: manifest[key] for key in ("description", "metadata", "definition")})


def nonnull_options(value):
    if isinstance(value, dict):
        return {key: nonnull_options(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [nonnull_options(item) for item in value]
    return value


def canonical_definition(definition):
    if not isinstance(definition, dict):
        return None
    result = nonnull_options(definition)
    result.setdefault("tools", [])
    # These documented defaults are sometimes materialized in service readback.
    for key in ("temperature", "top_p"):
        if type(result.get(key)) in (int, float) and result[key] == 1:
            result.pop(key)
    if not result["tools"] and result.get("tool_choice", "auto") in ("auto", "none"):
        result["tool_choice"] = "none"
    return result


def equivalent(current, desired):
    return (canonical_definition(current.get("definition")) == canonical_definition(desired["definition"])
            and current.get("description", "") == desired["description"]
            and (current.get("metadata") or {}) == desired["metadata"])


def validate_version(value):
    require(isinstance(value, dict) and value.get("object") == "agent.version" and
            value.get("name") == AGENT_NAME, "Azure returned an unexpected agent version resource.")
    version_string(value.get("version"))
    require(isinstance(value.get("definition"), dict) and value["definition"].get("kind") == "prompt",
            "The existing agent is not a prompt agent; no hosted resource will be changed.")
    require(value.get("status") == "active" and value.get("draft", False) is False,
            "The agent version is not an active, released version. Inspect Foundry before proceeding.")
    return value


def deploy_agent(client, manifest):
    desired = version_payload(manifest)
    try:
        agent = client.request("GET", AGENT_ROUTE)
    except AzureHTTPError as error:
        if error.status != 404:
            raise
        agent = None
    if agent is not None:
        require(agent.get("object") == "agent" and agent.get("name") == AGENT_NAME and
                agent.get("state") == "enabled", "The existing agent is malformed or disabled; inspect Foundry first.")
        versions = agent.get("versions")
        require(isinstance(versions, dict), "Azure did not return the current agent version; no write was attempted.")
        latest = validate_version(versions.get("latest"))
        if equivalent(latest, desired):
            return {"change": "unchanged", "agent_version": latest["version"]}
        require(isinstance(latest.get("metadata"), dict) and
                latest["metadata"].get("managed_by") == REPOSITORY,
                "Agent-name collision: the existing definition is not managed by this repository; no write was attempted.")
    # create_version is non-idempotent. There is deliberately no retry around this POST.
    try:
        created = validate_version(client.request("POST", AGENT_ROUTE + "/versions", desired))
        require(equivalent(created, desired),
                "Created version readback differs from the manifest. A version may exist; inspect Foundry before rerunning.")
        verified = validate_version(client.request("GET", AGENT_ROUTE + "/versions/" + created["version"]))
        require(verified["version"] == created["version"] and equivalent(verified, desired),
                "The persisted version does not match the create result. Inspect Foundry; no POST was retried.")
    except DemoError as error:
        error.mutation_possible = True
        raise
    return {"change": "created_version", "agent_version": verified["version"]}


def completed_smoke(response):
    require(isinstance(response, dict), "Smoke response must be a JSON object.")
    status = response.get("status")
    if status != "completed":
        safe_status = status if status in ("failed", "in_progress", "cancelled", "queued", "incomplete") else "unknown"
        raise DemoError(f"Smoke response status is {safe_status}, not completed. No inference retry was made.")
    require(response.get("object") == "response" and nonempty_text(response.get("id"), 200),
            "Smoke response is missing a response identifier or object type.")
    require(response.get("error") is None and response.get("incomplete_details") is None,
            "Smoke response reports an error or incomplete details despite completed status.")
    output = response.get("output")
    require(isinstance(output, list), "Smoke response has no structured assistant output.")
    texts = []
    for item in output:
        require(isinstance(item, dict), "Smoke output item is malformed.")
        if item.get("type") == "reasoning":
            require(item.get("status") in (None, "completed"), "Smoke reasoning item has not completed.")
            continue
        require(item.get("type") == "message" and item.get("role") == "assistant" and
                item.get("status") == "completed", "Smoke returned unfinished output or an unexpected tool/action item.")
        require(isinstance(item.get("content"), list), "Smoke message content is malformed.")
        for part in item["content"]:
            require(isinstance(part, dict) and part.get("type") == "output_text" and
                    isinstance(part.get("text"), str), "Smoke returned a refusal or unexpected content, not the benign answer.")
            if part["text"].strip():
                texts.append(part["text"].strip())
    require(bool(texts), "Smoke returned no nonempty completed assistant text.")
    usage = response.get("usage")
    if usage is not None:
        require(isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0
                for key in ("input_tokens", "output_tokens", "total_tokens")), "Smoke token usage is malformed.")
        require(usage["output_tokens"] <= SMOKE_MAX_OUTPUT_TOKENS, "Smoke exceeded the requested output token cap.")
        usage = {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
    return {"status": "completed", "output_characters": len("\n".join(texts)), "usage": usage}


def smoke_agent(client, version):
    version_string(version)
    request = {
        "agent_reference": {"type": "agent_reference", "name": AGENT_NAME, "version": version},
        "input": SMOKE_INPUT,
        "max_output_tokens": SMOKE_MAX_OUTPUT_TOKENS,
    }
    return completed_smoke(client.request("POST", "/openai/v1/responses", request, timeout=SMOKE_TIMEOUT))


def write_github_outputs(result):
    path = os.environ.get("GITHUB_OUTPUT")
    require(bool(path), "GitHub output file is unavailable; use --ci only inside the deployment workflow.")
    try:
        with Path(path).open("a", encoding="utf-8") as output:
            for key in ("status", "change", "agent_version", "smoke_status", "stage"):
                output.write(f"{key}={result[key]}\n")
    except OSError:
        raise DemoError("Cannot record GitHub step outputs; inspect the sanitized console result.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate all local contracts offline; no Azure calls.")
    mode.add_argument("--deploy", action="store_true", help="Explicitly create a version only if the manifest differs.")
    parser.add_argument("--smoke", action="store_true", help="With --deploy, make ONE bounded, billable response request.")
    parser.add_argument("--ci", action="store_true", help="Validate dispatch/OIDC inputs and write safe GitHub outputs.")
    args = parser.parse_args(argv)
    result = {"status": "blocked", "stage": "validation", "change": "not_attempted", "agent": AGENT_NAME,
              "model": MODEL_NAME, "agent_version": "", "smoke_status": "not_run" if args.smoke else "not_requested"}
    client, exit_code = None, 0
    try:
        require(not args.smoke or args.deploy, "--smoke requires --deploy; --check never calls Azure.")
        config, manifest, _evals = load_contracts()
        if args.ci:
            validate_ci_environment(os.environ)
            require(not args.deploy or args.smoke == (os.environ["DEMO_SMOKE"] == "true"),
                    "The smoke flag does not match the validated workflow input.")
        require(not (args.deploy and os.environ.get("GITHUB_ACTIONS") == "true") or args.ci,
                "GitHub deployments must use --ci and its dispatch/OIDC guards.")
        result.update(project_endpoint=config["project_endpoint"], agents_api_version=API_VERSION, region=REGION)
        if args.check:
            result.update(status="configured", stage="offline_check")
        else:
            result["stage"] = "authentication"
            client = AzureClient(config, get_access_token())
            result["stage"] = "deployment"
            result.update(deploy_agent(client, manifest))
            result.update(status="deployed", portal_url=PORTAL_URL + f"/{AGENT_NAME}/build?version=" + result["agent_version"])
            if args.smoke:
                result.update(stage="smoke", smoke_status="pending")
                result["smoke"] = smoke_agent(client, result["agent_version"])
                result["smoke_status"] = "completed"
            result["stage"] = "complete"
    except DemoError as error:
        exit_code = 1
        result["error"] = str(error)
        if result["stage"] == "deployment" and error.mutation_possible:
            result["change"] = "unverified"
        if result["stage"] == "smoke":
            result["smoke_status"] = "failed"
        print("Azure demo: " + str(error), file=sys.stderr)
    finally:
        if client is not None:
            client.clear_token()
    print(json.dumps(result, indent=2))
    if args.ci:
        try:
            write_github_outputs(result)
        except DemoError as error:
            print("Azure demo: " + str(error), file=sys.stderr)
            return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
