import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_demo as demo

CONFIG, MANIFEST, EVALS = demo.load_contracts()
FAKE_TOKEN = "test-token-in-memory-only"


def version(number="1", payload=None):
    return {
        "object": "agent.version", "name": demo.AGENT_NAME, "version": number,
        "id": f"{demo.AGENT_NAME}:{number}", "created_at": 1_788_823_000,
        "status": "active", "draft": False,
        **copy.deepcopy(payload if payload is not None else demo.version_payload(MANIFEST)),
    }


def agent(latest=None):
    return {"object": "agent", "name": demo.AGENT_NAME, "state": "enabled",
            "versions": {"latest": latest if latest is not None else version()}}


def completed_response():
    return {
        "object": "response", "id": "resp_test_only", "status": "completed",
        "error": None, "incomplete_details": None,
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "A new version is created. [S1]"}]}],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    }


def ci_environment():
    return {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": demo.REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF_TYPE": "branch",
        "GITHUB_REF": "refs/heads/main", "DEMO_DEFAULT_BRANCH": "main",
        "DEMO_ENVIRONMENT": "azure-demo", "DEMO_SMOKE": "false",
        "AZURE_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
        "AZURE_TENANT_ID": demo.TENANT_ID, "AZURE_SUBSCRIPTION_ID": demo.SUBSCRIPTION_ID,
    }


class FakeResponse(io.BytesIO):
    def __init__(self, value, status=200):
        super().__init__(json.dumps(value).encode() if not isinstance(value, bytes) else value)
        self.status = status


class ContractTests(unittest.TestCase):
    def test_repository_contracts_validate_without_azure(self):
        with patch.object(demo, "get_access_token") as token:
            demo.load_contracts()
        token.assert_not_called()
        self.assertGreaterEqual(len(EVALS["scenarios"]), 6)

    def test_endpoint_is_exact_not_just_an_azure_looking_suffix(self):
        for endpoint in (
            "http://swedenfoundry93.services.ai.azure.com/api/projects/foundry-showcase",
            "https://swedenfoundry93.services.ai.azure.com.attacker.invalid/api/projects/foundry-showcase",
            "https://other.services.ai.azure.com/api/projects/foundry-showcase",
            "https://user:password@swedenfoundry93.services.ai.azure.com/api/projects/foundry-showcase",
            demo.PROJECT_ENDPOINT + "/", demo.PROJECT_ENDPOINT + "?redirect=elsewhere",
            demo.PROJECT_ENDPOINT + "#fragment", demo.PROJECT_ENDPOINT.replace(".com/", ".com:443/"),
            demo.PROJECT_ENDPOINT.replace("/api/", "\\api\\"),
            demo.PROJECT_ENDPOINT + "\n", None, [],
        ):
            config = copy.deepcopy(CONFIG)
            config["project_endpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(demo.DemoError):
                demo.validate_config(config)

    def test_config_rejects_wrong_schema_target_scope_proxy_and_unknown_fields(self):
        changes = (
            ("schema_version", True), ("schema_version", "1"), ("schema_version", 2),
            ("agents_api_version", "latest"), ("agents_api_version", "v1"),
            ("authentication_scope", "https://graph.microsoft.com/.default"),
            ("preferred_agent", "triage-bot"), ("resource_name", "other"),
            ("project_name", "other"), ("region", "eastus"),
            ("portal_url", "https://attacker.invalid"), ("gateway_endpoint", "https://public-proxy.invalid"),
            ("client_secret", "not-a-real-secret"),
        )
        for key, value in changes:
            config = copy.deepcopy(CONFIG)
            config[key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.validate_config(config)
        config = copy.deepcopy(CONFIG)
        del config["project_name"]
        with self.assertRaises(demo.DemoError):
            demo.validate_config(config)

    def test_snapshot_is_explicitly_point_in_time(self):
        config = copy.deepcopy(CONFIG)
        config["snapshot"] = {
            "kind": "point_in_time", "last_verified": "2026-09-08T00:00:00Z",
            "agents": [{"name": demo.AGENT_NAME, "model": demo.MODEL_NAME, "version": "1"}],
        }
        demo.validate_config(config)
        for key, value in (("kind", "live"), ("last_verified", "today"), ("agents", [])):
            bad = copy.deepcopy(config)
            bad["snapshot"][key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.validate_config(bad)
        config["snapshot"]["agents"] *= 2
        with self.assertRaises(demo.DemoError):
            demo.validate_config(config)

    def test_manifest_cannot_target_existing_agents_models_or_hosted_resources(self):
        for name in ("triage-bot", "deep-research-analyst", "foundry-concierge", "../field-guide-coach"):
            manifest = copy.deepcopy(MANIFEST)
            manifest["name"] = name
            with self.subTest(name=name), self.assertRaises(demo.DemoError):
                demo.validate_manifest(manifest)
        for key, value in (("kind", "hosted"), ("model", "gpt-5.4"), ("tools", [{"type": "web_search"}]),
                           ("tool_choice", "auto"), ("reasoning", {"effort": "high"}),
                           ("instructions", ""), ("temperature", 0.5)):
            manifest = copy.deepcopy(MANIFEST)
            manifest["definition"][key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.validate_manifest(manifest)

    def test_manifest_requires_ownership_and_integer_schema(self):
        for key, value in (("metadata", {}), ("schema_version", True), ("description", None)):
            manifest = copy.deepcopy(MANIFEST)
            manifest[key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.validate_manifest(manifest)

    def test_seed_rubrics_are_required_unique_and_not_remote_quality_claims(self):
        for key, value in (("execution", "automatic"), ("registered_in_foundry", True),
                           ("schema_version", True), ("agent", "triage-bot")):
            evals = copy.deepcopy(EVALS)
            evals[key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.validate_evals(evals)
        for mutation in ("missing-rubric", "duplicate", "empty-context"):
            evals = copy.deepcopy(EVALS)
            if mutation == "missing-rubric":
                del evals["scenarios"][0]["expected_behavior"]
            elif mutation == "duplicate":
                evals["scenarios"].append(copy.deepcopy(evals["scenarios"][0]))
            else:
                evals["scenarios"][0]["context"] = ""
            with self.subTest(mutation=mutation), self.assertRaises(demo.DemoError):
                demo.validate_evals(evals)

    def test_json_rejects_duplicate_keys_nonfinite_values_and_oversize_files(self):
        for text in ('{"name":"one","name":"two"}', '{"number":NaN}', '{"number":Infinity}', FAKE_TOKEN):
            with self.subTest(text=text), self.assertRaises(demo.DemoError) as error:
                demo.parse_json(text)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "contract.json"
            path.write_bytes(b" " * (demo.MAX_FILE_BYTES + 1))
            with self.assertRaises(demo.DemoError):
                demo.load_json(path)
            with self.assertRaises(demo.DemoError):
                demo.load_json(Path(folder) / "missing.json")

    def test_ci_inputs_are_checked_before_login(self):
        demo.validate_ci_environment(ci_environment())
        changes = (
            ("AZURE_CLIENT_ID", ""), ("AZURE_CLIENT_ID", None), ("AZURE_CLIENT_ID", "${{ secrets.MISSING }}"),
            ("AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000000"),
            ("AZURE_TENANT_ID", "11111111-1111-4111-8111-111111111111"),
            ("AZURE_SUBSCRIPTION_ID", "11111111-1111-4111-8111-111111111111"),
            ("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "someone/fork"),
            ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF_TYPE", "tag"),
            ("GITHUB_REF", "refs/heads/feature"), ("DEMO_DEFAULT_BRANCH", ""),
            ("DEMO_ENVIRONMENT", "production"), ("DEMO_SMOKE", "yes"),
            ("DEMO_SMOKE", "false; echo unsafe"), ("AZURE_CLIENT_SECRET", FAKE_TOKEN),
        )
        for key, value in changes:
            env = ci_environment()
            env[key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError) as error:
                demo.validate_ci_environment(env)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))


class AuthenticationTests(unittest.TestCase):
    def token_info(self):
        return {"accessToken": FAKE_TOKEN, "tenant": demo.TENANT_ID,
                "subscription": demo.SUBSCRIPTION_ID, "expires_on": str(int(time.time()) + 3_600)}

    def run_auth(self, info):
        reply = SimpleNamespace(returncode=0, stdout=json.dumps(info).encode(), stderr=b"")
        with patch.object(demo.shutil, "which", return_value="az"), \
                patch.object(demo.subprocess, "run", return_value=reply):
            return demo.get_access_token()

    def test_token_is_captured_not_in_arguments_logs_or_environment(self):
        reply = SimpleNamespace(returncode=0, stdout=json.dumps(self.token_info()).encode(), stderr=b"")
        output = io.StringIO()
        with patch.object(demo.shutil, "which", return_value="az"), \
                patch.object(demo.subprocess, "run", return_value=reply) as run, \
                redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(demo.get_access_token(), FAKE_TOKEN)
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ["account", "get-access-token"])
        self.assertIn(demo.AUTH_SCOPE, command)
        self.assertIn(demo.SUBSCRIPTION_ID, command)
        self.assertNotIn("--tenant", command)
        self.assertNotIn(FAKE_TOKEN, str(run.call_args))
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.kwargs["timeout"], demo.CLI_TIMEOUT)
        self.assertEqual(output.getvalue(), "")

    def test_bad_context_expired_and_invalid_tokens_fail_closed(self):
        for key, value in (
            ("tenant", "wrong"), ("tenant", None), ("subscription", "wrong"), ("subscription", []), ("accessToken", ""),
            ("accessToken", "token\r\nInjected: header"), ("accessToken", None),
            ("expires_on", "0"), ("expires_on", True), ("expires_on", "tomorrow"), ("expires_on", "9" * 5_000),
        ):
            info = self.token_info()
            info[key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError) as error:
                self.run_auth(info)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))

    def test_cli_failure_never_includes_captured_output(self):
        failures = (
            SimpleNamespace(returncode=1, stdout=FAKE_TOKEN.encode(), stderr=FAKE_TOKEN.encode()),
            SimpleNamespace(returncode=0, stdout=FAKE_TOKEN.encode(), stderr=b""),
        )
        for reply in failures:
            with patch.object(demo.shutil, "which", return_value="az"), \
                    patch.object(demo.subprocess, "run", return_value=reply), \
                    self.assertRaises(demo.DemoError) as error:
                demo.get_access_token()
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
        timeout = subprocess.TimeoutExpired("az", 45, output=FAKE_TOKEN, stderr=FAKE_TOKEN)
        with patch.object(demo.shutil, "which", return_value="az"), \
                patch.object(demo.subprocess, "run", side_effect=timeout), \
                self.assertRaises(demo.DemoError) as error:
            demo.get_access_token()
        self.assertNotIn(FAKE_TOKEN, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_missing_cli_is_actionable(self):
        with patch.object(demo.shutil, "which", return_value=None), self.assertRaisesRegex(demo.DemoError, "missing"):
            demo.get_access_token()


class TransportTests(unittest.TestCase):
    def test_authorization_goes_only_to_approved_endpoint_with_a_timeout(self):
        opener = Mock()
        opener.open.return_value = FakeResponse(agent())
        client = demo.AzureClient(CONFIG, FAKE_TOKEN, opener)
        client.request("GET", demo.AGENT_ROUTE)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, demo.PROJECT_ENDPOINT + demo.AGENT_ROUTE + "?api-version=" + demo.API_VERSION)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + FAKE_TOKEN)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], demo.REQUEST_TIMEOUT)
        client.clear_token()
        with self.assertRaises(demo.DemoError):
            client.request("GET", demo.AGENT_ROUTE)

    def test_other_hosts_agents_verbs_and_path_injection_are_refused(self):
        opener = Mock()
        client = demo.AzureClient(CONFIG, FAKE_TOKEN, opener)
        for method, route in (
            ("POST", "/agents/triage-bot/versions"), ("DELETE", demo.AGENT_ROUTE),
            ("POST", "/agents"), ("POST", demo.AGENT_ROUTE),
            ("GET", "https://attacker.invalid"), ("GET", "//attacker.invalid/path"),
            ("GET", demo.AGENT_ROUTE + "/versions/../other"), ("GET", demo.AGENT_ROUTE + "?next=other"),
        ):
            with self.subTest(route=route), self.assertRaises(demo.DemoError):
                client.request(method, route, {} if method == "POST" else None)
        opener.open.assert_not_called()

    def test_redirects_are_disabled_including_same_origin(self):
        with patch.object(demo.urllib.request, "build_opener") as build:
            demo.AzureClient(CONFIG, FAKE_TOKEN)
        self.assertIsInstance(build.call_args.args[0], demo.NoRedirect)
        handler = demo.NoRedirect()
        for location in ("https://attacker.invalid", demo.PROJECT_ENDPOINT + "/elsewhere"):
            self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, location))

    def test_http_errors_are_sanitized_and_post_is_never_retried(self):
        for status in (302, 400, 401, 403, 404, 409, 429, 500, 503):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError(
                demo.PROJECT_ENDPOINT, status, FAKE_TOKEN, {}, io.BytesIO(FAKE_TOKEN.encode()))
            client = demo.AzureClient(CONFIG, FAKE_TOKEN, opener)
            with self.subTest(status=status), self.assertRaises(demo.AzureHTTPError) as error:
                client.request("POST", demo.AGENT_ROUTE + "/versions", demo.version_payload(MANIFEST))
            self.assertEqual(error.exception.status, status)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
            self.assertIn("not retried", str(error.exception))
            opener.open.assert_called_once()

    def test_timeout_and_invalid_response_do_not_replay_a_post(self):
        for failure in (TimeoutError(FAKE_TOKEN), urllib.error.URLError(FAKE_TOKEN)):
            opener = Mock()
            opener.open.side_effect = failure
            with self.subTest(failure=type(failure)), self.assertRaises(demo.DemoError) as error:
                demo.AzureClient(CONFIG, FAKE_TOKEN, opener).request("POST", "/openai/v1/responses", {})
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
            self.assertIn("not retried", str(error.exception))
            opener.open.assert_called_once()
        for body in (FAKE_TOKEN.encode(), [], b" " * (demo.MAX_RESPONSE_BYTES + 1)):
            opener = Mock()
            opener.open.return_value = FakeResponse(body)
            with self.subTest(body_type=type(body)), self.assertRaises(demo.DemoError) as error:
                demo.AzureClient(CONFIG, FAKE_TOKEN, opener).request("POST", "/openai/v1/responses", {})
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
            opener.open.assert_called_once()

    def test_out_of_bound_timeout_is_rejected_before_network(self):
        opener = Mock()
        for timeout in (0, 61, True, None):
            with self.subTest(timeout=timeout), self.assertRaises(demo.DemoError):
                demo.AzureClient(CONFIG, FAKE_TOKEN, opener).request("GET", demo.AGENT_ROUTE, timeout=timeout)
        opener.open.assert_not_called()


class DeploymentTests(unittest.TestCase):
    def test_equivalent_latest_does_not_create_a_version(self):
        current = version()
        current["definition"]["temperature"] = 1.0
        current["definition"]["top_p"] = 1
        current["definition"]["rai_config"] = None
        current["definition"]["reasoning"]["summary"] = None
        del current["definition"]["tools"]
        del current["definition"]["tool_choice"]
        client = Mock()
        client.request.return_value = agent(current)
        self.assertEqual(demo.deploy_agent(client, MANIFEST), {"change": "unchanged", "agent_version": "1"})
        client.request.assert_called_once_with("GET", demo.AGENT_ROUTE)

    def test_missing_agent_uses_explicit_create_version_then_reads_persistent_version(self):
        client = Mock()
        client.request.side_effect = [demo.AzureHTTPError(404, "GET"), version(), version()]
        result = demo.deploy_agent(client, MANIFEST)
        self.assertEqual(result, {"change": "created_version", "agent_version": "1"})
        calls = client.request.call_args_list
        self.assertEqual(calls[1].args, ("POST", demo.AGENT_ROUTE + "/versions", demo.version_payload(MANIFEST)))
        self.assertEqual(calls[2].args, ("GET", demo.AGENT_ROUTE + "/versions/1"))
        self.assertNotIn("schema_version", calls[1].args[2])
        self.assertNotIn("name", calls[1].args[2])

    def test_changed_owned_manifest_creates_once_and_next_run_is_idempotent(self):
        old = version()
        old["definition"]["instructions"] = "Previous managed instructions."
        client = Mock()
        client.request.side_effect = [agent(old), version("2"), version("2"), agent(version("2"))]
        self.assertEqual(demo.deploy_agent(client, MANIFEST)["change"], "created_version")
        self.assertEqual(demo.deploy_agent(client, MANIFEST), {"change": "unchanged", "agent_version": "2"})
        self.assertEqual(sum(call.args[0] == "POST" for call in client.request.call_args_list), 1)

    def test_same_name_unowned_agent_cannot_be_overwritten(self):
        old = version()
        old["metadata"] = {}
        client = Mock()
        client.request.return_value = agent(old)
        with self.assertRaisesRegex(demo.DemoError, "collision"):
            demo.deploy_agent(client, MANIFEST)
        client.request.assert_called_once()

    def test_auth_or_permission_failure_is_not_treated_as_absence(self):
        for status in (401, 403, 429, 500):
            client = Mock()
            client.request.side_effect = demo.AzureHTTPError(status, "GET")
            with self.subTest(status=status), self.assertRaises(demo.AzureHTTPError):
                demo.deploy_agent(client, MANIFEST)
            client.request.assert_called_once()

    def test_malformed_disabled_draft_and_nonactive_agents_fail_without_writes(self):
        for alteration in ("malformed", "disabled", "draft", "failed", "hosted", "bad-version"):
            current = agent()
            if alteration == "malformed":
                current["versions"] = []
            elif alteration == "disabled":
                current["state"] = "disabled"
            elif alteration == "draft":
                current["versions"]["latest"]["draft"] = True
            elif alteration == "failed":
                current["versions"]["latest"]["status"] = "failed"
            elif alteration == "hosted":
                current["versions"]["latest"]["definition"]["kind"] = "hosted"
            else:
                current["versions"]["latest"]["version"] = "1\nstatus=deployed"
            client = Mock()
            client.request.return_value = current
            with self.subTest(alteration=alteration), self.assertRaises(demo.DemoError):
                demo.deploy_agent(client, MANIFEST)
            client.request.assert_called_once()

    def test_create_failure_is_not_retried(self):
        client = Mock()
        client.request.side_effect = [demo.AzureHTTPError(404, "GET"), demo.AzureHTTPError(503, "POST")]
        with self.assertRaises(demo.AzureHTTPError):
            demo.deploy_agent(client, MANIFEST)
        self.assertEqual(client.request.call_count, 2)

    def test_mismatched_create_or_persisted_definition_is_not_success(self):
        bad = version()
        bad["definition"]["instructions"] = "Not the submitted definition."
        for replies in (
            [demo.AzureHTTPError(404, "GET"), bad],
            [demo.AzureHTTPError(404, "GET"), version(), bad],
            [demo.AzureHTTPError(404, "GET"), version(), version("different")],
        ):
            client = Mock()
            client.request.side_effect = replies
            with self.subTest(reply_count=len(replies)), self.assertRaises(demo.DemoError):
                demo.deploy_agent(client, MANIFEST)
            self.assertEqual(sum(call.args[0] == "POST" for call in client.request.call_args_list), 1)

    def test_failed_readback_marks_possible_mutation_without_replaying_post(self):
        client = Mock()
        client.request.side_effect = [
            demo.AzureHTTPError(404, "GET"), version(), demo.AzureHTTPError(403, "GET"),
        ]
        with self.assertRaises(demo.DemoError) as error:
            demo.deploy_agent(client, MANIFEST)
        self.assertTrue(error.exception.mutation_possible)
        self.assertEqual(sum(call.args[0] == "POST" for call in client.request.call_args_list), 1)

    def test_nondefault_behavior_changes_are_not_ignored(self):
        current = version()
        desired = demo.version_payload(MANIFEST)
        for key, value in (("temperature", 0.3), ("tools", [{"type": "web_search"}]),
                           ("reasoning", {"effort": "high"}), ("instructions", "Changed.")):
            changed = copy.deepcopy(current)
            changed["definition"][key] = value
            with self.subTest(field=key):
                self.assertFalse(demo.equivalent(changed, desired))


class SmokeTests(unittest.TestCase):
    def test_smoke_is_one_version_pinned_minimal_low_output_request(self):
        client = Mock()
        client.request.return_value = completed_response()
        result = demo.smoke_agent(client, "1")
        client.request.assert_called_once()
        call = client.request.call_args
        self.assertEqual(call.args[:2], ("POST", "/openai/v1/responses"))
        payload = call.args[2]
        self.assertEqual(payload["agent_reference"],
                         {"type": "agent_reference", "name": demo.AGENT_NAME, "version": "1"})
        self.assertNotIn("model", payload)
        self.assertEqual(payload["max_output_tokens"], 160)
        self.assertEqual(set(payload), {"agent_reference", "input", "max_output_tokens"})
        self.assertEqual(call.kwargs["timeout"], demo.SMOKE_TIMEOUT)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("text", result)

    def test_http_success_is_not_response_completion(self):
        for status in (None, "incomplete", "failed", "in_progress", "queued", "cancelled", FAKE_TOKEN):
            response = completed_response()
            response["status"] = status
            with self.subTest(status=status), self.assertRaises(demo.DemoError) as error:
                demo.completed_smoke(response)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))
        with self.assertRaises(demo.DemoError):
            demo.completed_smoke([])

    def test_completed_but_empty_error_or_unfinished_output_is_not_success(self):
        for mutation in ("empty-output", "top-level-text-only", "missing-id", "error", "incomplete-details",
                         "unfinished-message", "blank-text", "refusal", "tool-call", "non-assistant"):
            response = completed_response()
            message = response["output"][0]
            if mutation in ("empty-output", "top-level-text-only"):
                response["output"] = []
                response["output_text"] = "This convenience field alone is insufficient."
            elif mutation == "missing-id":
                del response["id"]
            elif mutation == "error":
                response["error"] = {"message": FAKE_TOKEN}
            elif mutation == "incomplete-details":
                response["incomplete_details"] = {"reason": "max_output_tokens"}
            elif mutation == "unfinished-message":
                message["status"] = "in_progress"
            elif mutation == "blank-text":
                message["content"][0]["text"] = " "
            elif mutation == "refusal":
                message["content"] = [{"type": "refusal", "refusal": "Refused."}]
            elif mutation == "tool-call":
                response["output"].append({"type": "function_call", "name": "unapproved"})
            else:
                message["role"] = "user"
            with self.subTest(mutation=mutation), self.assertRaises(demo.DemoError) as error:
                demo.completed_smoke(response)
            self.assertNotIn(FAKE_TOKEN, str(error.exception))

    def test_reasoning_item_before_message_is_not_mistaken_for_answer(self):
        response = completed_response()
        response["output"].insert(0, {"type": "reasoning", "summary": []})
        self.assertEqual(demo.completed_smoke(response)["status"], "completed")

    def test_invalid_usage_and_excess_output_fail(self):
        for key, value in (("output_tokens", 161), ("input_tokens", -1), ("total_tokens", "120")):
            response = completed_response()
            response["usage"][key] = value
            with self.subTest(field=key), self.assertRaises(demo.DemoError):
                demo.completed_smoke(response)
        response = completed_response()
        del response["usage"]
        self.assertIsNone(demo.completed_smoke(response)["usage"])

    def test_failed_smoke_does_not_retry_inference(self):
        client = Mock()
        response = completed_response()
        response["status"] = "incomplete"
        client.request.return_value = response
        with self.assertRaises(demo.DemoError):
            demo.smoke_agent(client, "1")
        client.request.assert_called_once()


class CommandTests(unittest.TestCase):
    def run_main(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = demo.main(args)
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_check_is_offline_and_does_not_imply_deployment(self):
        with patch.object(demo, "get_access_token") as token, patch.object(demo, "AzureClient") as client:
            code, result, _errors = self.run_main(["--check"])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "configured")
        self.assertEqual(result["change"], "not_attempted")
        token.assert_not_called()
        client.assert_not_called()

    def test_check_cannot_smoke(self):
        with patch.object(demo, "get_access_token") as token:
            code, result, _errors = self.run_main(["--check", "--smoke"])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "blocked")
        token.assert_not_called()

    def test_missing_ci_identity_blocks_before_azure(self):
        with tempfile.TemporaryDirectory() as folder:
            env = ci_environment()
            env["AZURE_CLIENT_ID"] = ""
            env["GITHUB_OUTPUT"] = str(Path(folder) / "outputs")
            with patch.dict(os.environ, env, clear=True), patch.object(demo, "get_access_token") as token:
                code, result, _errors = self.run_main(["--deploy", "--ci"])
            self.assertIn("status=blocked", Path(env["GITHUB_OUTPUT"]).read_text())
        self.assertEqual(code, 1)
        self.assertEqual(result["change"], "not_attempted")
        token.assert_not_called()

    def test_github_deploy_cannot_omit_ci_guards(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(demo, "get_access_token") as token:
            code, result, _errors = self.run_main(["--deploy"])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "blocked")
        token.assert_not_called()

    def test_smoke_failure_still_reports_successful_deployment_honestly(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(demo, "get_access_token", return_value=FAKE_TOKEN), \
                patch.object(demo, "AzureClient") as client, \
                patch.object(demo, "deploy_agent", return_value={"change": "created_version", "agent_version": "1"}), \
                patch.object(demo, "smoke_agent", side_effect=demo.DemoError("Smoke did not complete.")):
            code, result, errors = self.run_main(["--deploy", "--smoke"])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "deployed")
        self.assertEqual(result["smoke_status"], "failed")
        self.assertEqual(result["agent_version"], "1")
        client.return_value.clear_token.assert_called_once()
        self.assertNotIn(FAKE_TOKEN, json.dumps(result) + errors)

    def test_ambiguous_create_does_not_claim_deployed(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(demo, "get_access_token", return_value=FAKE_TOKEN), \
                patch.object(demo, "AzureClient"), \
                patch.object(demo, "deploy_agent", side_effect=demo.AzureHTTPError(503, "POST")):
            code, result, _errors = self.run_main(["--deploy"])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["change"], "unverified")

    def test_read_permission_failure_does_not_claim_a_write_was_attempted(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
                patch.object(demo, "get_access_token", return_value=FAKE_TOKEN), \
                patch.object(demo, "AzureClient"), \
                patch.object(demo, "deploy_agent", side_effect=demo.AzureHTTPError(403, "GET")):
            code, result, _errors = self.run_main(["--deploy"])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["change"], "not_attempted")

    def test_no_mode_or_conflicting_modes_cannot_deploy(self):
        for args in ([], ["--check", "--deploy"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit), \
                    patch.object(demo, "get_access_token") as token:
                demo.main(args)
            token.assert_not_called()


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_manual_guarded_oidc_without_artifact_uploads(self):
        workflow = (demo.ROOT / ".github" / "workflows" / "deploy-azure-demo.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        for trigger in ("\n  push:", "\n  pull_request:", "\n  schedule:", "\n  workflow_run:"):
            self.assertNotIn(trigger, workflow)
        self.assertIn("environment: azure-demo", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("github.event.repository.default_branch", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("requirements.txt", workflow)
        self.assertIn("--check --ci", workflow)
        self.assertIn("--deploy --ci --smoke", workflow)
        self.assertLess(workflow.index("--check --ci"), workflow.index("uses: azure/login@"))
        self.assertNotIn("client-secret:", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertIn("persist-credentials: false", workflow)
        for action in ("actions/checkout", "actions/setup-python", "azure/login"):
            self.assertRegex(workflow, action + r"@[0-9a-f]{40}")


if __name__ == "__main__":
    unittest.main()
