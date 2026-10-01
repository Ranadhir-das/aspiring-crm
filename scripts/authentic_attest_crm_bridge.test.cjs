const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, 'authentic_attest_crm_bridge.gs'), 'utf8');
function harness(fetch) {
  const context = vm.createContext({UrlFetchApp: {fetch}, console});
  vm.runInContext(source, context);
  return context;
}
test('maps existing fields and leaves WhatsApp in Sheet only', () => {
  const c = harness();
  const p = c.aaCrmPayload_([' Test ', '+91 9876543210', 'WA', ' Delhi ', ' India ', 'a@example.com'],
    {name: 0, phone: 1, whatsapp: 2, city: 3, country: 4, email: 5});
  assert.deepEqual(JSON.parse(JSON.stringify(p)), {name: 'Test', phone: '+91 9876543210',
    email: 'a@example.com', location: 'Delhi, India', service: 'APOSTILLE',
    source: 'AUTHENTIC_ATTEST', campaign: 'WEBSITE'});
});
test('sends key only as header and recognizes CRM duplicate success', () => {
  const c = harness((url, options) => {
    assert.equal(options.headers['X-Api-Key'], 'test-secret');
    assert.equal(options.followRedirects, false);
    assert.equal(options.muteHttpExceptions, true);
    assert.ok(!options.payload.includes('test-secret'));
    return {getResponseCode: () => 202, getContentText: () => '{"lead_id":42,"is_duplicate":true}'};
  });
  assert.equal(c.aaCrmSend_({url: 'https://crm.example.com/api/v1/public/leads/', key: 'test-secret'}, {}).status, 'SYNCED_DUPLICATE');
});
for (const [code, state] of [[400, 'REVIEW_HTTP'], [401, 'REVIEW_HTTP'], [403, 'REVIEW_HTTP'],
  [302, 'REVIEW_HTTP'], [429, 'RETRY'], [500, 'RETRY']]) {
  test(`HTTP ${code} is safely recorded as ${state}`, () => {
    const c = harness(() => ({getResponseCode: () => code}));
    assert.equal(c.aaCrmSend_({}, {}).status, state);
  });
}
test('network exceptions do not escape or expose secrets', () => {
  const c = harness(() => {throw new Error('sensitive response');});
  const r = c.aaCrmSend_({}, {});
  assert.equal(r.status, 'RETRY');
  assert.equal(r.http, 'NETWORK_ERROR');
});
test('an unexpected 202 is not mistaken for a saved lead', () => {
  const c = harness(() => ({getResponseCode: () => 202, getContentText: () => '<html>unexpected</html>'}));
  assert.equal(c.aaCrmSend_({}, {}).status, 'REVIEW_RESPONSE');
});

function workerHarness(fetch) {
  const c = harness(fetch);
  const original = ['Jane', '+919876543210', 'WA', 'Delhi', 'India', 'a@example.com'];
  const cells = [original.concat(['', '', '', '', '', ''])];
  let released = 0;
  c.LockService = {getScriptLock: () => ({tryLock: () => true, releaseLock: () => released++})};
  c.SpreadsheetApp = {flush() {}};
  c.console = {log() {}};
  c.aaCrmConfig_ = () => ({properties: {getProperty: () => '2'}});
  c.aaCrmColumns_ = () => ({name: 0, phone: 1, whatsapp: 2, city: 3, country: 4, email: 5, sync: 6});
  c.aaCrmSheet_ = () => ({getLastRow: () => 2, getLastColumn: () => 12,
    getRange: (row, col, count, width) => ({
      getDisplayValues: () => cells.map(r => r.map(String)),
      setValues: values => values[0].forEach((v, i) => {cells[row - 2][col - 1 + i] = v;})
    })});
  return {c, cells, original, released: () => released};
}
test('worker preserves Sheet data and skips already successful rows', () => {
  let calls = 0;
  const h = workerHarness(() => {
    calls++;
    return {getResponseCode: () => 202, getContentText: () => '{"lead_id":42,"is_duplicate":false}'};
  });
  h.c.syncAuthenticAttestCrm();
  h.c.syncAuthenticAttestCrm();
  assert.deepEqual(h.cells[0].slice(0, 6), h.original);
  assert.equal(h.cells[0][6], 'SYNCED');
  assert.equal(calls, 1);
  assert.equal(h.released(), 2);
});
test('worker persists failure, waits for cooldown, then allows manual retry', () => {
  let calls = 0;
  const h = workerHarness(() => {calls++; throw new Error('offline');});
  h.c.syncAuthenticAttestCrm();
  assert.equal(h.cells[0][6], 'RETRY');
  assert.equal(h.cells[0][7], 'NETWORK_ERROR');
  assert.ok(Date.parse(h.cells[0][10]) > Date.now());
  h.c.syncAuthenticAttestCrm();
  assert.equal(calls, 1);
  h.cells[0][6] = 'PENDING';
  h.cells[0][10] = '';
  h.c.syncAuthenticAttestCrm();
  assert.equal(calls, 2);
  assert.deepEqual(h.cells[0].slice(0, 6), h.original);
});
