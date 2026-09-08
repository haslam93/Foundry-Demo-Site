import test from 'node:test';
import assert from 'node:assert/strict';
import {
  agentKey, buildAgentRequest, createPreferences, extractReply, formatDate, httpsUrl,
  isStale, matchesQuery, nextAgentPage, normalizeAgent, sourceState, validateDemoConfig,
} from '../assets/core.js';

const endpoint = 'https://swedenfoundry93.services.ai.azure.com/api/projects/foundry-showcase';
const agent = { name: 'field-guide-coach', version: '2' };

test('unsafe URLs are rejected', () => {
  for (const value of ['javascript:alert(1)', 'http://example.com', 'https://user:password@example.com']) {
    assert.throws(() => httpsUrl(value));
  }
});

test('freshness cannot be green for missing, stale or future timestamps', () => {
  const now = Date.parse('2026-09-07T12:00:00Z');
  assert.equal(isStale('2026-09-07T06:00:00Z', 48, now), false);
  for (const value of [null, 'invalid', '2026-09-01T00:00:00Z', '2026-09-09T00:00:00Z']) {
    assert.equal(isStale(value, 48, now), true);
  }
  assert.equal(sourceState({ status: 'error', checked_at: new Date(now).toISOString() }, 48, now), 'error');
});

test('search supports common Foundry abbreviations and multiple terms', () => {
  assert.equal(matchesQuery({ title: 'Microsoft Agent Framework', topics: ['Python'] }, 'MAF python'), true);
  assert.equal(matchesQuery({ title: 'API gateway', summary: 'Token quotas' }, 'APIM quotas'), true);
  assert.equal(matchesQuery({ title: 'Evaluation datasets' }, 'evals'), true);
  assert.equal(matchesQuery({ title: 'Foundry Local' }, 'hosted agents'), false);
});

test('invalid dates remain explicitly unknown', () => {
  assert.equal(formatDate('bad'), 'Not available');
  assert.equal(formatDate(null), 'Not available');
});

test('demo tokens can only target the configured Azure project', () => {
  const config = { project_endpoint: endpoint, resource_name: 'swedenfoundry93',
    project_name: 'foundry-showcase', authentication_scope: 'https://ai.azure.com/.default',
    agents_api_version: '2025-11-15-preview' };
  assert.equal(validateDemoConfig(config).project_endpoint, endpoint);
  for (const project_endpoint of ['https://attacker.example/api/projects/foundry-showcase',
    endpoint + '?forward=1', endpoint + '/other']) {
    assert.throws(() => validateDemoConfig({ ...config, project_endpoint }));
  }
});

test('pagination refuses credential forwarding outside the configured project', () => {
  assert.throws(() => nextAgentPage({ nextLink: 'https://other.example/agents' }, endpoint, endpoint, new Set()));
  assert.throws(() => nextAgentPage({ nextLink: endpoint + '/secrets' }, endpoint, endpoint, new Set()));
  const current = endpoint + '/agents?api-version=2025-11-15-preview';
  const seen = new Set();
  const next = nextAgentPage({ has_more: true, last_id: 'next agent' }, current, endpoint, seen);
  assert.equal(new URL(next).searchParams.get('after'), 'next agent');
  assert.throws(() => nextAgentPage({ has_more: true, last_id: 'next agent' }, current, endpoint, seen));
});

test('response requests pin versions and scope conversation state', () => {
  const request = buildAgentRequest(agent, 'Hello', 'Official sources', 'response-1');
  assert.equal(request.agent_reference.version, '2');
  assert.equal(request.previous_response_id, 'response-1');
  assert.equal(request.max_output_tokens, 2000);
  assert.equal(agentKey(agent), 'field-guide-coach@2');
  assert.notEqual(agentKey(agent), agentKey({ ...agent, version: '3' }));
  assert.throws(() => buildAgentRequest(agent, ''));
  assert.throws(() => buildAgentRequest(agent, 'x'.repeat(6001)));
  assert.throws(() => buildAgentRequest(agent, 'Hello', '', null, 100000));
});

test('incomplete and empty responses cannot advance a conversation', () => {
  assert.throws(() => extractReply({ id: 'r', status: 'incomplete', incomplete_details: { reason: 'max_output_tokens' } }));
  assert.throws(() => extractReply({ id: 'r', status: 'completed', output: [] }));
  const reply = extractReply({ id: 'r', status: 'completed',
    output: [{ type: 'message', content: [{ type: 'output_text', text: 'Hello' }] }] });
  assert.deepEqual(reply, { id: 'r', text: 'Hello', tokens: undefined });
});

test('agent inventory requires a real version', () => {
  assert.throws(() => normalizeAgent({ name: 'agent' }));
  assert.equal(normalizeAgent({ name: 'agent', versions: { latest: { version: 3, definition: { model: 'mini' } } } }).version, '3');
});

test('blocked and corrupt storage are reported without breaking the page', () => {
  let failures = 0;
  const store = createPreferences(() => { throw new Error('Storage blocked'); }, () => { failures += 1; });
  assert.deepEqual(store.read('saved', []), []);
  assert.equal(store.write('saved', []), false);
  assert.equal(failures, 2);
  const corrupt = createPreferences(() => ({ getItem: () => '{bad' }), () => { failures += 1; });
  assert.deepEqual(corrupt.read('saved', []), []);
  assert.equal(failures, 3);
});
