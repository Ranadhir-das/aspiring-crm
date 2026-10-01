/* Add to the EXISTING Apps Script project. Do not replace doPost.
 * A time trigger forwards already-saved rows; the Sheet remains authoritative.
 * See docs/authentic-attest-crm-bridge.md before installation.
 */
var AA_CRM_HEADERS = ['CRM Sync Status', 'CRM HTTP Status', 'CRM Attempts',
  'CRM Last Attempt', 'CRM Next Retry', 'CRM Lead ID'];

function aaCrmConfig_() {
  var p = PropertiesService.getScriptProperties();
  var url = (p.getProperty('CRM_LEADS_URL') || '').trim();
  // Public HTTPS only. Never redirect a request carrying the secret header.
  if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?\/api\/v1\/public\/leads\/$/i.test(url) ||
      /^https:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.|169\.254\.)/i.test(url)) {
    throw new Error('CRM_CONFIG_URL');
  }
  var key = p.getProperty('CRM_API_KEY');
  if (!key) throw new Error('CRM_CONFIG_KEY');
  var id = p.getProperty('CRM_SPREADSHEET_ID');
  var tab = p.getProperty('CRM_SHEET_NAME');
  if (!id || !tab) throw new Error('CRM_CONFIG_SHEET');
  var map = JSON.parse(p.getProperty('CRM_COLUMN_MAP') || '{}');
  ['name', 'phone', 'whatsapp', 'city', 'country', 'email'].forEach(function (field) {
    if (typeof map[field] !== 'string' || !map[field]) throw new Error('CRM_CONFIG_COLUMNS');
  });
  return {properties: p, url: url, key: key, id: id, tab: tab, map: map};
}

function aaCrmSheet_(config) {
  var sheet = SpreadsheetApp.openById(config.id).getSheetByName(config.tab);
  if (!sheet) throw new Error('CRM_CONFIG_SHEET');
  return sheet;
}

function aaCrmColumns_(sheet, config, install) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getDisplayValues()[0];
  var columns = {};
  Object.keys(config.map).forEach(function (field) {
    var header = config.map[field];
    var index = headers.indexOf(header);
    if (index < 0 || headers.lastIndexOf(header) !== index) throw new Error('CRM_CONFIG_COLUMNS');
    columns[field] = index;
  });
  var start = headers.indexOf(AA_CRM_HEADERS[0]);
  if (start < 0 && install) {
    if (AA_CRM_HEADERS.some(function (h) { return headers.indexOf(h) >= 0; })) {
      throw new Error('CRM_CONFIG_TRACKING_COLUMNS');
    }
    start = headers.length;
    var needed = start + AA_CRM_HEADERS.length - sheet.getMaxColumns();
    if (needed > 0) sheet.insertColumnsAfter(sheet.getMaxColumns(), needed);
    sheet.getRange(1, start + 1, 1, AA_CRM_HEADERS.length).setValues([AA_CRM_HEADERS]);
  } else if (start < 0 || AA_CRM_HEADERS.some(function (h, i) {
    return headers[start + i] !== h || headers.lastIndexOf(h) !== start + i;
  })) {
    throw new Error('CRM_CONFIG_TRACKING_COLUMNS');
  }
  columns.sync = start;
  return columns;
}

// Run once manually after reviewing compatibility with the existing doPost.
function installAuthenticAttestCrmBridge() {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var config = aaCrmConfig_();
    var sheet = aaCrmSheet_(config);
    aaCrmColumns_(sheet, config, true);
    // Do not send historical leads unless an administrator explicitly opts in.
    if (!config.properties.getProperty('CRM_FIRST_ROW')) {
      config.properties.setProperty('CRM_FIRST_ROW', String(Math.max(2, sheet.getLastRow() + 1)));
    }
    var exists = ScriptApp.getProjectTriggers().some(function (t) {
      return t.getHandlerFunction() === 'syncAuthenticAttestCrm';
    });
    if (!exists) ScriptApp.newTrigger('syncAuthenticAttestCrm').timeBased().everyMinutes(5).create();
    console.log('AA_CRM bridge installed');
  } finally { lock.releaseLock(); }
}

function aaCrmPayload_(row, columns) {
  function value(field) { return String(row[columns[field]] || '').trim(); }
  return {name: value('name'), phone: value('phone'), email: value('email'),
    location: [value('city'), value('country')].filter(Boolean).join(', '),
    service: 'APOSTILLE', source: 'AUTHENTIC_ATTEST', campaign: 'WEBSITE'};
  // whatsapp remains in its existing Sheet column; the CRM has no whatsapp field.
}

function aaCrmSend_(config, payload) {
  try {
    var response = UrlFetchApp.fetch(config.url, {
      method: 'post', contentType: 'application/json',
      headers: {'X-Api-Key': config.key}, payload: JSON.stringify(payload),
      muteHttpExceptions: true, followRedirects: false, validateHttpsCertificates: true
    });
    var code = response.getResponseCode();
    if (code === 202) {
      var data;
      try { data = JSON.parse(response.getContentText()); } catch (_) { data = {}; }
      if (Number.isSafeInteger(data.lead_id) && data.lead_id > 0 && typeof data.is_duplicate === 'boolean') {
        return {status: data.is_duplicate ? 'SYNCED_DUPLICATE' : 'SYNCED', http: code, lead: data.lead_id};
      }
      return {status: 'REVIEW_RESPONSE', http: code};
    }
    return {status: code === 429 || code === 408 || code >= 500 ? 'RETRY' : 'REVIEW_HTTP', http: code};
  } catch (_) {
    // Never log exceptions: they may contain request URLs or response data.
    return {status: 'RETRY', http: 'NETWORK_ERROR'};
  }
}

function syncAuthenticAttestCrm() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    var config = aaCrmConfig_();
    var sheet = aaCrmSheet_(config);
    var columns = aaCrmColumns_(sheet, config, false);
    var first = Number(config.properties.getProperty('CRM_FIRST_ROW'));
    if (!Number.isInteger(first) || first < 2) throw new Error('CRM_CONFIG_FIRST_ROW');
    var last = sheet.getLastRow();
    var deadline = Date.now() + 180000;
    var sent = 0;
    // Read in bounded chunks; at most three network requests per trigger.
    for (var base = first; base <= last && sent < 3 && Date.now() < deadline; base += 250) {
      var rows = sheet.getRange(base, 1, Math.min(250, last - base + 1), sheet.getLastColumn()).getDisplayValues();
      for (var i = 0; i < rows.length && sent < 3 && Date.now() < deadline; i++) {
        var row = rows[i];
        var state = row[columns.sync];
        if (state && state !== 'RETRY' && state !== 'SENDING' && state !== 'PENDING') continue;
        var due = Date.parse(row[columns.sync + 4]);
        if (Number.isFinite(due) && due > Date.now()) continue;
        var payload = aaCrmPayload_(row, columns);
        if (!payload.name && !payload.phone) continue;
        var attempts = (Number(row[columns.sync + 2]) || 0) + 1;
        var now = new Date();
        var range = sheet.getRange(base + i, columns.sync + 1, 1, AA_CRM_HEADERS.length);
        // Persist the attempt before fetching; stale SENDING retries after 10 minutes.
        range.setValues([['SENDING', '', attempts, now.toISOString(), new Date(now.getTime() + 600000).toISOString(), '']]);
        SpreadsheetApp.flush();
        var result = aaCrmSend_(config, payload);
        sent++;
        var retry = '';
        if (result.status === 'RETRY') {
          if (attempts >= 8) result.status = 'REVIEW_MAX_RETRIES';
          else retry = new Date(Date.now() + Math.min(86400000, 900000 * Math.pow(2, attempts - 1))).toISOString();
        }
        range.setValues([[result.status, result.http, attempts, now.toISOString(), retry, result.lead || '']]);
        SpreadsheetApp.flush();
        console.log(JSON.stringify({event: 'AA_CRM_SYNC', status: result.status, http: result.http, attempt: attempts}));
        // Avoid repeatedly hitting authentication problems or a shared-IP throttle.
        if (result.http === 429 || result.http === 401 || result.http === 403 || result.http >= 500 || result.http === 'NETWORK_ERROR') return;
      }
    }
  } catch (_) {
    console.log('AA_CRM worker stopped: check Script Properties, column mapping and Sheet permissions');
  } finally { lock.releaseLock(); }
}
