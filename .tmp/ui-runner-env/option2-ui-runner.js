const { chromium } = require('playwright');

(async () => {
  const state = {
    apps: [
      {
        id: 'csi_gateway',
        reference_key: 'csi-gateway',
        application_name: 'CSI Gateway',
        api_variant: 'core',
        endpoint_url: 'https://csi-perf.att.com',
        owner_team: 'Middleware Team',
        version: 'v1',
        tags: ['core'],
        is_active: true,
        updated_by: 'seed',
        last_updated: '2026-03-24T00:00:00Z',
        reference_count: 1,
      },
    ],
    seen: [],
  };

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const assert = (cond, msg) => {
    if (!cond) {
      throw new Error(msg);
    }
  };

  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    state.seen.push(`${method} ${path}`);

    const json = (obj, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(obj) });

    if (path === '/api/env/configs' && method === 'GET') return json({ references: [] });
    if (path === '/api/pipeline/runs' && method === 'GET') return json({ runs: [] });
    if (path === '/api/jira/tickets' && method === 'GET') return json({ tickets: [] });

    if (path === '/api/apps' && method === 'GET') {
      const includeInactive = url.searchParams.get('include_inactive') === 'true';
      const apps = includeInactive ? state.apps : state.apps.filter((a) => a.is_active);
      return json({ applications: apps });
    }

    if (path === '/api/apps' && method === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      const app = {
        id: body.application_key.replace(/-/g, '_'),
        reference_key: body.application_key,
        application_name: body.application_name,
        api_variant: body.api_variant || 'core',
        endpoint_url: body.endpoint_url,
        owner_team: body.owner_team || '',
        version: body.version || '',
        tags: body.tags || [],
        is_active: true,
        updated_by: body.updated_by || 'dashboard-ui',
        last_updated: '2026-03-24T21:40:00Z',
        reference_count: 1,
      };
      state.apps.push(app);
      return json(app);
    }

    if (path.startsWith('/api/apps/') && method === 'PATCH') {
      const key = path.split('/').pop();
      const body = JSON.parse(req.postData() || '{}');
      const app = state.apps.find((a) => a.reference_key === key);
      if (!app) return json({ detail: 'not found' }, 404);
      Object.assign(app, {
        application_name: body.application_name,
        endpoint_url: body.endpoint_url,
        api_variant: body.api_variant,
        owner_team: body.owner_team || '',
        version: body.version || '',
        tags: body.tags || [],
        last_updated: '2026-03-24T21:41:00Z',
      });
      return json(app);
    }

    if (path.startsWith('/api/apps/') && method === 'DELETE') {
      const key = path.split('/').pop();
      const app = state.apps.find((a) => a.reference_key === key);
      if (!app) return json({ detail: 'not found' }, 404);
      app.is_active = false;
      app.last_updated = '2026-03-24T21:42:00Z';
      return json({ archived_count: 1 });
    }

    return json({});
  });

  page.on('dialog', async (dialog) => {
    await dialog.accept();
  });

  await page.goto('http://127.0.0.1:8000/perf_test_dashboard.html', { waitUntil: 'domcontentloaded' });
  await page.getByTitle('Environment Configurations').click();

  await page.getByRole('button', { name: 'Add App' }).click();
  await page.getByLabel('Application Name').fill('UI Smoke App');
  await page.getByLabel('Application Key / Slug').fill('ui-smoke-app');
  await page.getByLabel('Endpoint URL').fill('https://ui-smoke.att.com');
  await page.getByLabel('API Variant').fill('core');
  await page.getByLabel('Tags (comma-separated)').fill('ui,smoke');
  await page.getByLabel('Owner / Team').fill('UI Team');
  await page.getByLabel('Version').fill('v1');
  await page.getByRole('button', { name: 'Create App' }).click();

  const createdAppButton = page.locator('button', { hasText: 'UI Smoke App' }).first();
  await createdAppButton.waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Edit App' }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((el) => el.textContent?.trim() === 'Edit App');
    return !!btn && !btn.disabled;
  });

  await page.getByRole('button', { name: 'Edit App' }).click();
  const slugInput = page.getByLabel('Application Key / Slug');
  assert(await slugInput.isDisabled(), 'Expected slug field disabled during edit');
  assert((await slugInput.inputValue()) === 'ui-smoke-app', 'Expected slug field value to be ui-smoke-app');

  await page.getByLabel('Application Name').fill('UI Smoke App Updated');
  await page.getByLabel('Endpoint URL').fill('https://ui-smoke-updated.att.com');
  await page.getByLabel('API Variant').fill('edge');
  await page.getByLabel('Tags (comma-separated)').fill('ui,updated');
  await page.getByLabel('Owner / Team').fill('UI Team B');
  await page.getByLabel('Version').fill('v2');
  await page.getByRole('button', { name: 'Save Changes' }).click();

  const updatedAppButton = page.locator('button', { hasText: 'UI Smoke App Updated' }).first();
  await updatedAppButton.waitFor({ state: 'visible' });

  await page.getByRole('button', { name: 'Delete App' }).click();

  await page.waitForFunction(() => {
    return !Array.from(document.querySelectorAll('button')).some((el) =>
      (el.textContent || '').includes('UI Smoke App Updated')
    );
  });
  assert(await page.getByRole('button', { name: 'Edit App' }).isDisabled(), 'Expected Edit App to be disabled with no selection');

  const calls = state.seen.join('\n');
  assert(calls.includes('POST /api/apps'), 'Expected POST /api/apps call');
  assert(calls.includes('PATCH /api/apps/ui-smoke-app'), 'Expected PATCH /api/apps/ui-smoke-app call');
  assert(calls.includes('DELETE /api/apps/ui-smoke-app'), 'Expected DELETE /api/apps/ui-smoke-app call');

  console.log(JSON.stringify({ ok: true, apiCalls: state.seen }, null, 2));
  await browser.close();
})();
