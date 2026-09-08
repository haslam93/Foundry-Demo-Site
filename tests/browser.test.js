import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdir, readFile } from 'node:fs/promises';
import { dirname, extname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const site = resolve(root, '_site');
const diagnostics = resolve(root, 'test-results');
const azure = 'https://swedenfoundry93.services.ai.azure.com';
const endpoint = `${azure}/api/projects/foundry-showcase`;
let server;
let browser;
let base;

before(async () => {
  await readFile(resolve(site, 'index.html'));
  await mkdir(diagnostics, { recursive: true });
  server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      if (!pathname.startsWith('/Foundry-Demo-Site/')) {
        response.writeHead(404).end();
        return;
      }
      const relative = pathname.slice('/Foundry-Demo-Site/'.length) || 'index.html';
      const path = resolve(site, relative);
      if (!path.startsWith(site + sep)) {
        response.writeHead(403).end();
        return;
      }
      const body = await readFile(path);
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
        '.json': 'application/json', '.xml': 'application/rss+xml' };
      response.writeHead(200, { 'Content-Type': `${types[extname(path)] || 'application/octet-stream'}; charset=utf-8` });
      response.end(body);
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end();
    }
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  base = `http://127.0.0.1:${server.address().port}/Foundry-Demo-Site/`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (server) await new Promise(done => server.close(done));
});

async function pageTest(name, run, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', ...options });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await run(page, context);
    assert.deepEqual(errors, [], 'No unhandled frontend exceptions');
  } catch (error) {
    await page.screenshot({ path: resolve(diagnostics, `${name}-failure.png`) });
    throw error;
  } finally {
    await context.close();
  }
}

async function ready(page, suffix = '') {
  await page.goto(base + suffix);
  await page.locator('.resource-card').first().waitFor();
  await page.waitForFunction(() => !document.querySelector('#btnConnect').disabled);
}

function inventory(names = ['field-guide-coach', 'triage-bot']) {
  return { data: names.map(name => ({
    name, versions: { latest: { version: '1', definition: { kind: 'prompt', model: 'test-mini' } } },
  })), has_more: false };
}
function completed(id, text = 'A grounded demo answer.') {
  return { id, status: 'completed', output: [
    { type: 'message', content: [{ type: 'output_text', text }] },
  ], usage: { total_tokens: 42 } };
}

test('Pages subpath loads real snapshots without automatic Azure requests', async () => pageTest('desktop', async page => {
  const requests = [];
  page.on('request', request => { if (request.url().startsWith(azure)) requests.push(request.url()); });
  await ready(page, '?clawpilotTheme=light');
  assert.equal(await page.locator('.resource-card').count(), 12);
  assert.ok(await page.locator('.release-card').count() >= 5);
  assert.ok(Number(await page.locator('#heroResourceCount').textContent()) >= 20);
  assert.equal(await page.locator('#sourceGrid .source-card').count(), 8);
  assert.deepEqual(requests, []);
  assert.match(await page.locator('#demoState').textContent(), /Not connected/);
  assert.equal(await page.locator('#liveControls').isHidden(), true);
  await page.screenshot({ path: resolve(diagnostics, 'desktop-light.png') });
  await page.locator('#briefing').screenshot({ path: resolve(diagnostics, 'daily-brief.png') });
}));

test('resource search, shareable filters and bookmarks persist', async () => pageTest('library', async page => {
  await ready(page);
  await page.locator('#resourceTopic').selectOption('API gateway');
  await page.waitForFunction(() => document.querySelector('#resourceResults').textContent.startsWith('0 ') === false);
  const filtered = await page.locator('.resource-card').count();
  assert.ok(filtered >= 2 && filtered < 12);
  assert.match(page.url(), /topic=API\+gateway/);
  await page.locator('.resource-card .save-button').first().click();
  await page.reload();
  await page.locator('.resource-card').first().waitFor();
  await page.locator('#resourceSavedOnly').click();
  assert.equal(await page.locator('.resource-card').count(), 1);
  assert.equal(await page.locator('.resource-card .save-button').getAttribute('aria-pressed'), 'true');
  await page.locator('#clearResourceFilters').click();
  await page.locator('#resourceSearch').fill('MAF');
  assert.ok(await page.locator('.resource-card').count() >= 1);
  await page.locator('#resourceSearch').fill('a-nonexistent-topic-012345');
  assert.match(await page.locator('#resourceGrid').textContent(), /No matching resources/);
}));

test('every topic shortcut opens the right library view and clears restrictive filters', async () => pageTest('topic-shortcuts', async page => {
  await ready(page);
  await page.locator('#resourceSearch').fill('nonexistent-query-012345');
  await page.locator('#resourceType').selectOption({ index: 1 });
  await page.locator('#resourceSavedOnly').click();
  const shortcuts = await page.locator('#topicChips .topic-chip, a[data-resource-topic]').all();
  assert.ok(shortcuts.length >= 10);
  for (const shortcut of shortcuts) {
    const topic = await shortcut.getAttribute('data-resource-topic') || await shortcut.textContent();
    await shortcut.click();
    assert.equal(await page.locator('#resourceTopic').inputValue(), topic);
    assert.equal(await page.locator('#resourceSearch').inputValue(), '');
    assert.equal(await page.locator('#resourceType').inputValue(), '');
    assert.equal(await page.locator('#resourceSavedOnly').getAttribute('aria-pressed'), 'false');
    assert.ok(await page.locator('.resource-card').count() > 0);
    assert.equal(new URL(page.url()).searchParams.get('topic'), topic);
    assert.equal(new URL(page.url()).hash, '#resources');
    const top = await page.locator('#resources').evaluate(node => node.getBoundingClientRect().top);
    assert.ok(top >= 0 && top < 200, 'The library heading is in view');
  }
}));

test('playbook progress, selected runbook and download are functional', async () => pageTest('playbook', async page => {
  await ready(page, '?book=hosted');
  assert.equal(await page.locator('[data-book="hosted"]').getAttribute('aria-pressed'), 'true');
  await page.locator('#playbookPanel input[type=checkbox]').first().check();
  assert.match(await page.locator('.playbook-progress').textContent(), /1 \//);
  await page.reload();
  await page.locator('#playbookPanel input[type=checkbox]').first().waitFor();
  assert.equal(await page.locator('#playbookPanel input[type=checkbox]').first().isChecked(), true);
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download runbook', exact: true }).click();
  const download = await downloadEvent;
  assert.equal(download.suggestedFilename(), 'foundry-hosted-runbook.md');
  assert.match(await readFile(await download.path(), 'utf8'), /https:\/\/learn.microsoft.com/);
  await page.getByRole('button', { name: 'Try this scenario in the live lab' }).click();
  assert.match(await page.locator('#prompt').inputValue(), /hosted/i);
  assert.equal(await page.locator('#liveControls').isHidden(), true);
  await page.locator('#playbooks').screenshot({ path: resolve(diagnostics, 'hosted-playbook.png') });
}));

test('command search, theme and narrow mobile layout work', async () => pageTest('mobile', async page => {
  await ready(page, '?clawpilotTheme=dark');
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  await page.keyboard.press('Control+k');
  assert.equal(await page.locator('#searchDialog').isVisible(), true);
  await page.locator('#globalSearch').fill('APIM');
  assert.ok(await page.locator('.palette-result').count() >= 1);
  await page.keyboard.press('Escape');
  await page.locator('#searchDialog').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#searchDialog').isHidden(), true);
  assert.equal(await page.locator('#globalSearch').inputValue(), 'APIM');
  for (const width of [375, 320]) {
    await page.setViewportSize({ width, height: 812 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `No page overflow at ${width}px`);
  }
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: resolve(diagnostics, 'mobile-dark.png') });
  await page.locator('#themeToggle').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
}));

test('blocked browser storage is visible and does not break navigation', async () => pageTest('storage', async page => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('Blocked', 'SecurityError'); } });
  });
  await ready(page);
  assert.equal(await page.locator('#storageNotice').isVisible(), true);
  await page.locator('#themeToggle').click();
  await page.locator('.resource-card .save-button').first().click();
  assert.match(await page.locator('#toast').textContent(), /session only/);
}));

test('a failed feed leaves the library usable and does not display green freshness', async () => pageTest('failed-feed', async page => {
  await page.route('**/news.json', route => route.fulfill({ status: 503, body: 'Unavailable' }));
  await ready(page);
  assert.match(await page.locator('#freshnessStatus').textContent(), /Feed unavailable/);
  assert.match(await page.locator('#freshnessStatus').getAttribute('class'), /error/);
  assert.equal(await page.locator('.resource-card').count(), 12);
  assert.match(await page.locator('#newsResultCount').textContent(), /unavailable/);
}));

test('outdated successful checks turn visibly stale', async () => pageTest('stale', async page => {
  const snapshot = JSON.parse(await readFile(resolve(site, 'news.json'), 'utf8'));
  snapshot.last_checked = '2020-01-01T00:00:00Z';
  snapshot.sources.forEach(source => {
    source.status = 'ok';
    source.checked_at = source.last_success_at = snapshot.last_checked;
  });
  await page.route('**/news.json', route => route.fulfill({ json: snapshot }));
  await ready(page);
  assert.match(await page.locator('#freshnessStatus').textContent(), /Refresh overdue/);
  assert.equal(await page.locator('#sourceGrid .health-dot.stale').count(), 8);
  assert.match(await page.locator('.release-card').first().textContent(), /Last-known version/);
}));

test('upstream HTML is rendered as text, not executed', async () => pageTest('source-markup', async page => {
  const snapshot = JSON.parse(await readFile(resolve(site, 'news.json'), 'utf8'));
  snapshot.items[0].title = '<img src=x onerror="window.sourceExecuted=true">';
  snapshot.items[0].date = new Date().toISOString().slice(0, 10);
  snapshot.items[0].published_at = new Date().toISOString();
  await page.route('**/news.json', route => route.fulfill({ json: snapshot }));
  await ready(page);
  assert.match(await page.locator('#newsGrid').textContent(), /<img src=x/);
  assert.equal(await page.evaluate(() => window.sourceExecuted), undefined);
  assert.equal(await page.locator('#newsGrid img').count(), 0);
}));

test('Azure calls are explicit, tokens are not persisted, and conversations are isolated', async () => pageTest('azure-flow', async page => {
  const posts = [];
  const auth = [];
  await page.route(`${endpoint}/agents?*`, route => {
    auth.push(route.request().headers().authorization);
    return route.fulfill({ json: inventory() });
  });
  await page.route(`${endpoint}/openai/v1/responses`, route => {
    posts.push(route.request().postDataJSON());
    auth.push(route.request().headers().authorization);
    return route.fulfill({ json: completed(`response-${posts.length}`) });
  });
  await ready(page);
  await page.locator('#tok').fill('fake-test-token-one');
  await page.locator('#btnConnect').click();
  await page.locator('#liveControls').waitFor();
  assert.equal(await page.locator('#tok').inputValue(), '');
  assert.equal(await page.locator('#agentSel option').count(), 2);
  assert.equal(posts.length, 0);
  await page.locator('#prompt').fill('Use the supplied sources to explain hosted agents.');
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelectorAll('.msg.agent').length === 1);
  assert.equal(posts[0].agent_reference.version, '1');
  assert.equal(posts[0].previous_response_id, undefined);
  assert.match(posts[0].input, /SOURCE CONTEXT/);
  assert.ok(posts[0].max_output_tokens <= 4000);
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelectorAll('.msg.agent').length === 2);
  assert.equal(posts[1].previous_response_id, 'response-1');
  await page.locator('#agentSel').selectOption('triage-bot@1');
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelectorAll('.msg.agent').length === 1);
  assert.equal(posts[2].previous_response_id, undefined);
  await page.locator('#btnReset').click();
  assert.equal(await page.locator('.msg.agent').count(), 0);
  await page.locator('#btnDisconnect').click();
  assert.equal(await page.locator('#liveControls').isHidden(), true);
  await page.locator('#tok').fill('fake-test-token-two');
  await page.locator('#btnConnect').click();
  await page.locator('#liveControls').waitFor();
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelectorAll('.msg.agent').length === 1);
  assert.equal(posts[3].previous_response_id, undefined);
  assert.equal(auth.at(-1), 'Bearer fake-test-token-two');
  assert.equal(await page.evaluate(() => JSON.stringify(localStorage).includes('fake-test-token')), false);
  assert.equal(await page.locator('#requestPreview').textContent().then(text => text.includes('fake-test-token')), false);
}));

test('Azure authentication failures clear the token and show actionable diagnostics', async () => pageTest('azure-auth', async page => {
  await page.route(`${endpoint}/agents?*`, route => route.fulfill({ status: 401, json: { error: 'Unauthorized' } }));
  await ready(page);
  await page.locator('#tok').fill('fake-expired-token');
  await page.locator('#btnConnect').click();
  await page.waitForFunction(() => document.querySelector('#demoState').textContent.includes('HTTP 401'));
  assert.match(await page.locator('#demoState').textContent(), /fresh/);
  assert.equal(await page.locator('#tok').inputValue(), '');
  assert.equal(await page.locator('#liveControls').isHidden(), true);
}));

test('an incomplete response never becomes the next turn context', async () => pageTest('azure-incomplete', async page => {
  const posts = [];
  await page.route(`${endpoint}/agents?*`, route => route.fulfill({ json: inventory(['field-guide-coach']) }));
  await page.route(`${endpoint}/openai/v1/responses`, route => {
    posts.push(route.request().postDataJSON());
    return route.fulfill({ json: posts.length === 1
      ? { id: 'incomplete-response', status: 'incomplete', incomplete_details: { reason: 'max_output_tokens' } }
      : completed('good-response') });
  });
  await ready(page);
  await page.locator('#tok').fill('fake-token');
  await page.locator('#btnConnect').click();
  await page.locator('#liveControls').waitFor();
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelector('#chatLog').textContent.includes('did not complete'));
  await page.locator('#btnSend').click();
  await page.waitForFunction(() => document.querySelectorAll('.msg.agent').length === 1);
  assert.equal(posts[1].previous_response_id, undefined);
}));

test('unsafe demo configuration is blocked before any token can be sent', async () => pageTest('bad-config', async page => {
  const config = JSON.parse(await readFile(resolve(site, 'demo/config.json'), 'utf8'));
  config.project_endpoint = 'https://attacker.example/api/projects/foundry-showcase';
  await page.route('**/demo/config.json', route => route.fulfill({ json: config }));
  const requests = [];
  page.on('request', request => { if (request.url().includes('attacker.example')) requests.push(request.url()); });
  await page.goto(base);
  await page.locator('.resource-card').first().waitFor();
  assert.equal(await page.locator('#btnConnect').isDisabled(), true);
  assert.match(await page.locator('#demoState').textContent(), /invalid/);
  assert.deepEqual(requests, []);
}));
