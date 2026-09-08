export function httpsUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password) {
    throw new Error('Only HTTPS source URLs without credentials are supported.');
  }
  return url.href;
}

export function formatDate(value, withTime = false) {
  const date = new Date(value?.length === 10 ? `${value}T00:00:00Z` : value);
  if (!value || Number.isNaN(date.valueOf())) return 'Not available';
  return new Intl.DateTimeFormat(undefined, withTime
    ? { dateStyle: 'medium', timeStyle: 'short' }
    : { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}

export function isStale(timestamp, maxHours = 48, now = Date.now()) {
  const time = Date.parse(timestamp);
  return !Number.isFinite(time) || time > now + 300_000 || now - time > maxHours * 3_600_000;
}

export function matchesQuery(record, query) {
  const text = [record.title, record.summary, record.goal, record.source_label, record.type,
    ...(record.topics || []), ...(record.tags || []), ...(record.languages || [])]
    .filter(Boolean).join(' ').toLowerCase();
  const expanded = query.toLowerCase().replace(/\bmaf\b/g, 'agent framework')
    .replace(/\bapim\b/g, 'api gateway').replace(/\bevals\b/g, 'evaluation');
  return expanded.trim().split(/\s+/).every(word => text.includes(word));
}

export function sourceState(source, staleHours = 48, now = Date.now()) {
  if (source.status === 'error') return 'error';
  if (source.status === 'warning') return 'warning';
  return isStale(source.last_success_at || source.checked_at, staleHours, now) ? 'stale' : 'ok';
}

export function normalizeAgent(row) {
  const latest = row.versions?.latest;
  if (!row.name || !latest?.version) throw new Error('The agent listing is missing its name or version.');
  return {
    name: row.name, version: String(latest.version),
    model: latest.definition?.model || latest.definition?.kind || 'Agent',
    kind: latest.definition?.kind || 'unknown',
  };
}

export function validateDemoConfig(config) {
  const endpoint = new URL(httpsUrl(config.project_endpoint));
  const expectedHost = `${config.resource_name}.services.ai.azure.com`;
  if (endpoint.hostname !== expectedHost || endpoint.port || endpoint.search || endpoint.hash ||
      endpoint.pathname !== `/api/projects/${config.project_name}` ||
      config.authentication_scope !== 'https://ai.azure.com/.default' ||
      !/^\d{4}-\d{2}-\d{2}(?:-preview)?$/.test(config.agents_api_version)) {
    throw new Error('Demo configuration is not a recognized Foundry project. No token was sent.');
  }
  return { ...config, project_endpoint: endpoint.href.replace(/\/$/, '') };
}

export function nextAgentPage(payload, currentUrl, endpoint, seenCursors) {
  const nextLink = payload.nextLink || payload.next_link;
  if (nextLink) {
    const next = new URL(nextLink, currentUrl);
    const project = new URL(endpoint);
    if (next.origin !== project.origin || next.pathname !== `${project.pathname}/agents` ||
        next.username || next.password || next.hash) {
      throw new Error('Refused an agent pagination link outside the configured project.');
    }
    if (seenCursors.has(next.href)) throw new Error('The service repeated an agent-list page.');
    seenCursors.add(next.href);
    return next.href;
  }
  if (!payload.has_more) return null;
  const cursor = payload.last_id || payload.data?.at(-1)?.id || payload.data?.at(-1)?.name;
  if (!cursor || seenCursors.has(cursor)) throw new Error('The service returned an invalid agent-list cursor.');
  seenCursors.add(cursor);
  const next = new URL(currentUrl);
  next.searchParams.set('after', cursor);
  return next.href;
}

export function agentKey(agent) {
  return `${agent.name}@${agent.version}`;
}

export function buildAgentRequest(agent, prompt, context = '', previousResponseId = null, budget = 2000) {
  if (!agent?.name || !agent.version) throw new Error('Connect and select a versioned agent first.');
  if (!prompt?.trim()) throw new Error('Enter a message before sending.');
  if (prompt.length > 6000) throw new Error('Keep your message under 6,000 characters.');
  if (![1000, 2000, 4000].includes(budget)) throw new Error('Choose a supported output budget.');
  const request = {
    agent_reference: { type: 'agent_reference', name: agent.name, version: agent.version },
    input: context ? `${prompt.trim()}\n\n${context.slice(0, 10000)}` : prompt.trim(),
    max_output_tokens: budget,
  };
  if (previousResponseId) request.previous_response_id = previousResponseId;
  return request;
}

export function extractReply(response) {
  if (response.status !== 'completed') {
    const reason = response.incomplete_details?.reason || response.status || 'unknown';
    throw new Error(`The response did not complete (${reason}). No conversation state was advanced. ` +
      'For an output limit, increase the output budget or ask a shorter question.');
  }
  const text = (response.output || []).filter(part => part.type === 'message')
    .flatMap(part => part.content || [])
    .map(part => part.type === 'refusal' ? part.refusal : part.type === 'output_text' ? part.text : '')
    .filter(Boolean).join('\n');
  if (!response.id || !text) throw new Error('The service returned no completed assistant text.');
  return { id: response.id, text, tokens: response.usage?.total_tokens };
}

export function httpHelp(status) {
  const help = {
    400: 'The service rejected the request. Check the agent version, API contract and message size.',
    401: 'Your access token is expired or has the wrong audience. Obtain a fresh ai.azure.com token and reconnect.',
    403: 'Your identity cannot access this project. Check project RBAC and network restrictions; do not disable them.',
    404: 'The selected project or agent version no longer exists. Reconnect to reload the live inventory.',
    408: 'The request timed out. The server may still finish and incur usage.',
    429: 'The deployment is rate limited or out of quota. Wait before retrying; no automatic paid retries are made.',
    500: 'The service could not complete the operation. Inspect Foundry traces before retrying.',
    503: 'The service is temporarily unavailable. Check deployment readiness before retrying.',
  };
  return `HTTP ${status}. ${help[status] || 'The service returned an error. Check the project in the Foundry portal.'}`;
}

export function createPreferences(getStorage, onFailure) {
  return {
    read(key, fallback) {
      try {
        const value = getStorage().getItem(`foundry-guide:${key}`);
        return value === null ? fallback : JSON.parse(value);
      } catch (error) {
        onFailure(error);
        return fallback;
      }
    },
    write(key, value) {
      try {
        getStorage().setItem(`foundry-guide:${key}`, JSON.stringify(value));
        return true;
      } catch (error) {
        onFailure(error);
        return false;
      }
    },
  };
}
