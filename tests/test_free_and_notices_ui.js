'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const panelJs = fs.readFileSync(path.join(root, 'panel.js'), 'utf8');
const i18nJs = fs.readFileSync(path.join(root, 'panel_i18n.js'), 'utf8');

function createHarness(options = {}) {
  const nodes = new Map();
  const storage = new Map();
  if (options.saved) storage.set('eh-panel-language', options.saved);
  const element = id => {
    if (!nodes.has(id)) {
      const node = {
        id, hidden: false, disabled: false, open: false, textContent: '', value: '', className: '',
        href: '', target: '', rel: '', type: '', children: [], attributes: {}, dataset: {},
        isRoot: true, parent: null,
        get isConnected() { return this.parent ? this.parent.isConnected : this.isRoot; },
        classList: { toggle() {} }, reset() {}, focus() {}, select() {}, scrollIntoView() {},
        showModal() { this.open = true; },
        close() { this.open = false; },
        addEventListener() {},
        removeAttribute(name) { delete this.attributes[name]; },
        getAttribute(name) { return this.attributes[name] ?? null; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        append(...items) { for (const c of items) c.parent = this; this.children.push(...items); },
        replaceChildren(...items) { for (const c of this.children) c.parent = null; this.children = []; this.append(...items); },
        querySelector(sel) { return element(id + sel); },
      };
      node.elements = new Proxy({}, { get: (_, name) => element(id + ':' + name) });
      nodes.set(id, node);
    }
    return nodes.get(id);
  };
  const fixture = {
    signed_in: options.signed_in ?? true,
    account: 'account-free',
    username: 'Alice',
    version: '0.28.0',
    endpoint: 'https://api.example.invalid',
    pending: null,
    notice: null,
    server_notices: options.server_notices || [],
    credentials: { api_ready: true, device_ready: true, key_id: 'key-1' },
    connection: { clean: true, running: false, status: 'offline' },
    onboarding: { stage: 'connect' },
    updates: options.updates || { status: 'source_manual' },
    terms_document: { available: true, version: 'v1', sha256: 'hash' },
    cloud_checked_at: 1000,
    cloud: {
      access_mode: options.access_mode || 'free',
      subscription: {
        billing: 'lifetime_frames', metering: 'frames', enabled: true,
        expires_at: null, period_ends_at: null,
        frame_limit: 10000000, charged_frames: 42, remaining: 9999958,
      },
      keys: [], device: { registered: true, enabled: true },
      terms: { current: true, version: 'v1', sha256: 'hash' },
      appeals: { available: true, eligible: false },
      recovery: { verified: true, available: true, email_masked: 'a***@example.invalid' },
    },
  };
  const document = {
    documentElement: { lang: 'en' },
    cookie: '',
    getElementById: element,
    querySelectorAll: () => [],
    createElement: tag => {
      const node = element(tag + ':' + nodes.size);
      node.tagName = tag.toUpperCase();
      node.isRoot = false;
      return node;
    },
  };
  const context = vm.createContext({
    fixture, document, URL,
    location: { hash: '', pathname: '/', origin: 'http://127.0.0.1:8123' },
    sessionStorage: { getItem: () => '', setItem() {} },
    localStorage: {
      getItem: k => { if (options.storageBlocked) throw Error('blocked'); return storage.get(k) || null; },
      setItem: (k, v) => { if (options.storageBlocked) throw Error('blocked'); storage.set(k, v); },
    },
    history: { replaceState() {} },
    navigator: { language: options.browser || 'en-US', onLine: true, clipboard: { writeText: async () => {} } },
    window: { scrollTo() {} },
    confirm: () => false,
    fetch: async url => ({
      ok: true,
      json: async () => (url === '/api/state' ? fixture : url === '/api/appeals' ? { appeals: [], pending_confirmed: false } : { status: 'ok' }),
    }),
    setTimeout, clearTimeout, setInterval() {}, AbortController,
  });
  vm.runInContext(i18nJs, context, { filename: 'panel_i18n.js' });
  vm.runInContext(panelJs, context, { filename: 'panel.js' });
  const run = code => vm.runInContext(code, context);
  run('state=fixture;render()');
  return { element, fixture, storage, run, switchLang: lang => run(`setPanelLanguage(${JSON.stringify(lang)})`) };
}

const treeText = node => [node.textContent, ...node.children.map(treeText)].join(' ');

async function main() {
  let passed = 0;
  async function test(name, fn) { await fn(); passed++; console.log('ok - ' + name); }

  await test('free access mode removes quota/frame copy and shows clean status cards', () => {
    const app = createHarness({ saved: 'en', access_mode: 'free' });
    assert.equal(app.element('subscription-status').textContent, 'Free · Active');
    assert.equal(app.element('remaining-label').textContent, 'Registration email');
    assert.equal(app.element('remaining').textContent, 'a***@example.invalid');
    assert.equal(app.element('expiry-label').textContent, 'Fair use');
    assert.equal(app.element('expiry').textContent, '1 concurrent session');
    assert.doesNotMatch(app.element('billing-period').textContent, /frames|quota|10,000,000|42/i);
    assert.match(app.element('billing-period').textContent, /100% Free · Register & Use/);
    assert.equal(app.element('payment-info-section').hidden, true);
    assert.equal(app.element('activation-section').hidden, true);
    assert.equal(app.element('overview-access').textContent, '100% Free · Active');

    app.switchLang('zh-CN');
    assert.equal(app.element('subscription-status').textContent, '免费开放');
    assert.equal(app.element('remaining-label').textContent, '注册邮箱');
    assert.equal(app.element('expiry-label').textContent, '使用规则');
    assert.equal(app.element('expiry').textContent, '单并发会话');
    assert.doesNotMatch(app.element('billing-period').textContent, /帧|额度/);
    assert.equal(app.element('overview-access').textContent, '免费开放 · 正常可用');
  });

  await test('free mode registration feedback confirms free access in both languages', async () => {
    const app = createHarness({ saved: 'en', access_mode: 'free' });
    await app.run("act('register')");
    assert.match(app.element('feedback').textContent, /100% free — register & use right away/);
    app.switchLang('zh-CN');
    assert.match(app.element('feedback').textContent, /完全免费、注册即用/);
  });

  await test('quota mode keeps payment/activation cards visible for future capacity toggles', () => {
    const app = createHarness({ saved: 'en', access_mode: 'quota' });
    assert.equal(app.element('payment-info-section').hidden, false);
    assert.equal(app.element('activation-section').hidden, false);
    assert.equal(app.element('subscription-status').textContent, 'Active');
    assert.equal(app.element('expiry-label').textContent, 'Valid until');
  });

  await test('notification bell modal preserves history, tracks unread badge, validates https links, and works signed out', () => {
    const notices = [
      {
        id: 'notice-1',
        level: 'maintenance',
        title: { en: 'Planned Upgrade', 'zh-CN': '计划维护升级' },
        body: { en: '<img src=x onerror=alert(1)> Back soon.', 'zh-CN': '很快恢复。' },
        url: 'https://status.example.org/eh',
        url_label: { en: 'Status Page', 'zh-CN': '查看状态' },
        updated_at: '2026-11-21',
        dismissible: true,
      },
      {
        id: 'notice-2',
        level: 'warning',
        title: 'Bad link test',
        body: 'Unsafe link must not render.',
        url: 'javascript:alert(1)',
        dismissible: false,
      },
    ];
    const app = createHarness({ saved: 'en', signed_in: false, server_notices: notices });
    // Unread badge shows 2; modal starts closed and opens on bell click
    assert.equal(app.element('notice-unread-badge').hidden, false);
    assert.equal(app.element('notice-unread-badge').textContent, '2');
    assert.equal(app.element('notice-modal').open, false);
    app.element('open-notices').onclick();
    assert.equal(app.element('notice-modal').open, true);

    const box = app.element('server-notices');
    assert.equal(box.hidden, false);
    assert.equal(box.children.length, 2);
    const textEn = treeText(box);
    assert.match(textEn, /Maintenance/);
    assert.match(textEn, /Planned Upgrade/);
    assert.match(textEn, /<img src=x onerror=alert\(1\)> Back soon\./);
    assert.match(textEn, /Status Page/);

    // Verify first notice has valid https link; second notice has NO link and NO mark-read button
    const firstBody = box.children[0].children[0];
    const link = firstBody.children.find(n => n.tagName === 'A');
    assert.ok(link);
    assert.equal(link.href, 'https://status.example.org/eh');
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');

    const secondBody = box.children[1].children[0];
    assert.equal(secondBody.children.find(n => n.tagName === 'A'), undefined);
    assert.equal(box.children[1].children.find(n => n.tagName === 'BUTTON'), undefined);

    // Switch to zh-CN updates notice copy live
    app.switchLang('zh-CN');
    const textZh = treeText(box);
    assert.match(textZh, /维护通知/);
    assert.match(textZh, /计划维护升级/);
    assert.match(textZh, /很快恢复/);
    assert.match(textZh, /查看状态/);

    // Marking notice-1 read decrements unread badge to 1, persists in localStorage, AND keeps notice-1 in History!
    const markReadBtn = box.children[0].children.find(n => n.tagName === 'BUTTON');
    assert.ok(markReadBtn);
    markReadBtn.onclick();
    assert.equal(app.element('notice-unread-badge').textContent, '1');
    assert.deepEqual(JSON.parse(app.storage.get('eh-read-notices')), ['notice-1']);
    // Still 2 items in "All" tab (history preserved!)
    assert.equal(box.children.length, 2);
    // Switching to "Unread" shows only notice-2
    app.element('notice-tab-unread').onclick();
    assert.equal(box.children.length, 1);
    assert.match(treeText(box), /Bad link test/);
    // Switching to "History (Read)" shows notice-1 while still published on server
    app.element('notice-tab-read').onclick();
    assert.equal(box.children.length, 1);
    assert.match(treeText(box), /计划维护升级/);

    // When the operator withdraws (unpublishes) notice-1 on the server, it immediately disappears from ALL client views!
    app.fixture.server_notices = [];
    app.run('render()');
    assert.equal(box.children.length, 0);
    assert.equal(box.hidden, true);
    app.element('notice-tab-all').onclick();
    assert.equal(box.children.length, 0);
    assert.equal(app.element('notices-empty').hidden, false);
  });

  await test('appeals modal and corner version popover open and close cleanly', async () => {
    const app = createHarness({ saved: 'en', updates: { status: 'available', version: '0.29.0' } });
    // Corner version pill highlights update and toggles popover
    assert.match(app.element('version-pill').className, /has-update/);
    assert.equal(app.element('version-badge').textContent, 'Update');
    app.element('update-popover').hidden = true;
    app.element('version-pill').onclick();
    assert.equal(app.element('update-popover').hidden, false);
    app.element('update-popover-close').onclick();
    assert.equal(app.element('update-popover').hidden, true);

    // Appeals modal opens from Account security card button and closes cleanly
    assert.equal(app.element('appeals-section').open, false);
    app.element('appeal-open-history').onclick();
    assert.equal(app.element('appeals-section').open, true);
    app.element('appeals-modal-close').onclick();
    assert.equal(app.element('appeals-section').open, false);
  });

  await test('notices work gracefully when localStorage is blocked', () => {
    const app = createHarness({
      saved: 'en', storageBlocked: true,
      server_notices: [{ id: 'n-blocked', level: 'info', title: 'Hello', body: 'World', dismissible: true }],
    });
    assert.equal(app.element('notice-unread-badge').textContent, '1');
    const box = app.element('server-notices');
    box.children[0].children.find(n => n.tagName === 'BUTTON').onclick();
    assert.equal(app.element('notice-unread-badge').hidden, true);
  });

  console.log(`${passed} free-access, notice-modal & appeals-modal UI checks passed`);
}

main().catch(err => { console.error(err); process.exitCode = 1; });
