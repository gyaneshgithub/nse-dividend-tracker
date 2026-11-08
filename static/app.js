/* Dividend Tracker dashboard behaviour. Static asset, vanilla JS, no build step. */
const LS_HOLDINGS = 'dividendTracker.holdings.v1';
const LS_SYNCED = 'dividendTracker.synced.v1';

let state = { holdings: [], year: null, historyTicker: null, analysisOpen: false, analysisYear: null, insightOpen: false, insightYear: null, insightTab: 'overview', insightData: null, coachOpen: false, coachYear: null, coachData: null, sortKey: null, sortDir: 'asc', summaryProjections: null, summaryAnalysis: null };
let summaryProjectionToken = 0;
let refreshing = false;
const syncTimers = {};
const pendingDefaults = {};
// Deleted tickers are tombstoned for this tab so an in-flight dividend
// fetch or a stale poll cannot write the holding back to ChromaDB.
// The value is the deletion time; a remote holding edited after that
// (for example re-added from another window) is allowed through.
const deletedTickers = new Map();
// All writes and deletes for one ticker run in order, so a delete always
// lands after any save that was already queued.
const saveQueues = new Map();
// Tickers present in the last server snapshot. A local holding that was
// here before but is missing from the server was deleted elsewhere.
let syncedTickers = new Set();
const INITIAL = window.__INITIAL__ || { holdings: [], revision: 0 };
const POLL_MS = 3000;
let lastRevision = null;
let pollTimer = null;
let pollDebounce = null;
let pollInFlight = false;
let pendingRemote = null;
let syncReady = false;
let lastCalendarYear = new Date().getFullYear();

const el = {
  yearFilter: document.getElementById('year-filter'),
  yearNote: document.getElementById('year-note'),
  incomeBridge: document.getElementById('income-bridge'),
  clearYearBtn: document.getElementById('clear-year'),
  sumCount: document.getElementById('sum-count'),
  sumIncome: document.getElementById('sum-income'),
  sumIncomeLabel: document.getElementById('sum-income-label'),
  projectionCard: document.getElementById('projection-card'),
  sumProjected: document.getElementById('sum-projected'),
  sumProjectedDelta: document.getElementById('sum-projected-delta'),
  projectionScenarios: document.getElementById('projection-scenarios'),
  sumMonthly: document.getElementById('sum-monthly'),
  sumMonthlyLabel: document.getElementById('sum-monthly-label'),
  sumYield: document.getElementById('sum-yield'),
  tableHead: document.getElementById('table-head'),
  holdingsBody: document.getElementById('holdings-body'),
  addForm: document.getElementById('add-form'),
  addInput: document.getElementById('add-input'),
  addShares: document.getElementById('add-shares'),
  formMsg: document.getElementById('form-msg'),
  errorBanner: document.getElementById('error-banner'),
  errorBannerTitle: document.getElementById('error-banner-title'),
  errorBannerMessage: document.getElementById('error-banner-message'),
  errorBannerHint: document.getElementById('error-banner-hint'),
  errorBannerMeta: document.getElementById('error-banner-meta'),
  errorRetry: document.getElementById('error-retry'),
  errorCopy: document.getElementById('error-copy'),
  errorDismiss: document.getElementById('error-dismiss'),
  emptyState: document.getElementById('empty-state'),
  tableWrap: document.getElementById('table-wrap'),
  scrollHint: document.getElementById('scroll-hint'),
  importBtn: document.getElementById('import-btn'),
  importModal: document.getElementById('import-modal'),
  importClose: document.getElementById('import-close'),
  importCancel: document.getElementById('import-cancel'),
  importRun: document.getElementById('import-run'),
  importYear: document.getElementById('import-year'),
  importText: document.getElementById('import-text'),
  importFile: document.getElementById('import-file'),
  importMsg: document.getElementById('import-msg'),
  importResult: document.getElementById('import-result'),
  refreshAllBtn: document.getElementById('refresh-all'),
  historyModal: document.getElementById('history-modal'),
  historyTitle: document.getElementById('history-title'),
  historySub: document.getElementById('history-sub'),
  historyBody: document.getElementById('history-body'),
  historyClose: document.getElementById('history-close'),
  dbStatus: document.getElementById('db-status'),
  dbStatusText: document.getElementById('db-status-text'),
  analysisBtn: document.getElementById('analysis-btn'),
  analysisModal: document.getElementById('analysis-modal'),
  analysisClose: document.getElementById('analysis-close'),
  analysisSub: document.getElementById('analysis-sub'),
  analysisYear: document.getElementById('analysis-year'),
  analysisBody: document.getElementById('analysis-body'),
  analysisRefreshBtn: document.getElementById('analysis-refresh'),
  analysisSearchInput: document.getElementById('analysis-search-input'),
  analysisSearchBtn: document.getElementById('analysis-search-btn'),
  analysisChips: document.getElementById('analysis-chips'),
  analysisSearchResults: document.getElementById('analysis-search-results'),
  insightBtn: document.getElementById('insight-btn'),
  insightModal: document.getElementById('insight-modal'),
  insightClose: document.getElementById('insight-close'),
  insightSub: document.getElementById('insight-sub'),
  insightYear: document.getElementById('insight-year'),
  insightTabs: document.getElementById('insight-tabs'),
  insightBody: document.getElementById('insight-body'),
  insightReload: document.getElementById('insight-reload'),
  coachBtn: document.getElementById('coach-btn'),
  coachBadge: document.getElementById('coach-badge'),
  coachModal: document.getElementById('coach-modal'),
  coachClose: document.getElementById('coach-close'),
  coachSub: document.getElementById('coach-sub'),
  coachYear: document.getElementById('coach-year'),
  coachBody: document.getElementById('coach-body'),
  coachReload: document.getElementById('coach-reload'),
};

// ---------- storage ----------
function loadHoldings() {
  try { return JSON.parse(localStorage.getItem(LS_HOLDINGS)) || []; } catch (e) { return []; }
}
function saveHoldings() { localStorage.setItem(LS_HOLDINGS, JSON.stringify(state.holdings)); }
function loadSyncedTickers() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_SYNCED));
    return new Set(Array.isArray(saved) ? saved : []);
  } catch (e) {
    return new Set();
  }
}
function saveSyncedTickers() {
  localStorage.setItem(LS_SYNCED, JSON.stringify([...syncedTickers]));
}
function persist() { saveHoldings(); render(); if (state.insightOpen) loadInsight(); }

function getHolding(ticker) { return state.holdings.find(h => h.ticker === ticker); }
function findIndex(ticker) { return state.holdings.findIndex(h => h.ticker === ticker); }

// ---------- local ChromaDB sync ----------
function timeValue(value) {
  const parsed = Date.parse(value || 0);
  return isNaN(parsed) ? 0 : parsed;
}

function touch(h, quarter) {
  const now = new Date().toISOString();
  h.updatedAt = now;
  if (quarter) {
    h.quarterUpdatedAt = h.quarterUpdatedAt || {};
    h.quarterUpdatedAt[quarter] = now;
  }
}

function quarterTime(h, key) {
  const raw = h.quarterUpdatedAt && h.quarterUpdatedAt[key];
  return raw ? timeValue(raw) : null;
}

function setDbStatus(kind, text, title) {
  el.dbStatus.className = 'db-status ' + kind;
  el.dbStatusText.textContent = text;
  if (title) el.dbStatus.title = title;
}

// ---------- user-facing messages and fetch errors ----------
function showFormMessage(text, kind) {
  el.formMsg.textContent = text || '';
  el.formMsg.className = kind || '';
}

const ERROR_FALLBACKS = {
  invalid_symbol: {
    title: 'That ticker looks invalid',
    hint: 'Use 2–15 letters or digits with optional hyphens, e.g. RELIANCE or BAJAJ-AUTO. Spaces and .NS are removed automatically.',
  },
  no_history: {
    title: 'No dividend history found',
    hint: 'The ticker may be delisted, renamed, or may never have paid a dividend. Check the symbol on nseindia.com and try again.',
  },
  unknown_ticker: {
    title: 'Ticker not found upstream',
    hint: 'Check that the NSE symbol is current, then press Retry.',
  },
  network: {
    title: 'Could not reach the dividend source',
    hint: 'Check your internet connection, then press Retry. If you are behind a proxy or firewall, allow stockanalysis.com.',
  },
  rate_limited: {
    title: 'The dividend source is rate-limiting requests',
    hint: 'Wait a minute, then press Retry. Data already fetched is cached for 6 hours.',
  },
  source_down: {
    title: 'The dividend source is having trouble',
    hint: 'This is usually temporary. Press Retry in a moment.',
  },
  upstream: {
    title: 'Dividend fetch failed',
    hint: 'Press Retry. If it keeps failing, copy the debug info and check the server log.',
  },
};

let fetchErrorTickers = [];
let fetchErrorInfo = null;

function describeFailure(failure) {
  const code = failure.code || 'upstream';
  const fallback = ERROR_FALLBACKS[code] || ERROR_FALLBACKS.upstream;
  return {
    code,
    title: fallback.title,
    message: failure.message || 'The request failed.',
    hint: failure.hint || fallback.hint,
  };
}

function reportFetchErrors(failures) {
  const list = (failures || []).filter(Boolean);
  if (!list.length) return;
  const first = describeFailure(list[0]);
  const tickers = [...new Set(list.map(f => f.ticker))];
  fetchErrorTickers = tickers;
  fetchErrorInfo = {
    ...first,
    tickers,
    details: list.map(f => ({ ticker: f.ticker, code: f.code, message: f.message, hint: f.hint })),
  };
  const single = tickers.length === 1;
  const mixed = list.some(f => (f.code || 'upstream') !== first.code);
  el.errorBannerTitle.textContent = single ? `Couldn't load ${tickers[0]}` : `Couldn't load ${tickers.length} stocks`;
  el.errorBannerMessage.textContent = (!single && mixed)
    ? 'Some stocks could not be refreshed. The first reason is shown below.'
    : first.message;
  el.errorBannerHint.textContent = first.hint;
  el.errorBannerMeta.textContent = single ? '' : 'Failed: ' + tickers.join(', ');
  el.errorRetry.disabled = false;
  el.errorRetry.textContent = 'Retry';
  el.errorBanner.hidden = false;
}

function clearFetchError(ticker) {
  if (!fetchErrorInfo) return;
  const remaining = fetchErrorInfo.details.filter(d => d.ticker !== ticker);
  if (!remaining.length) {
    hideFetchError();
    return;
  }
  // Rebuild from the remaining details so the title, message and hint stay
  // consistent when one stock recovers but others still fail.
  reportFetchErrors(remaining.map(d => ({ ...d, ok: false })));
}

function hideFetchError() {
  fetchErrorTickers = [];
  fetchErrorInfo = null;
  el.errorBanner.hidden = true;
}

function errorDebugText() {
  const backend = (INITIAL.storage && INITIAL.storage.backend) || 'unknown';
  const lines = [
    'Dividend Tracker fetch error',
    'Time: ' + new Date().toISOString(),
    'Page: ' + location.href,
    'Tickers: ' + (fetchErrorTickers.join(', ') || 'n/a'),
    'Code: ' + (fetchErrorInfo ? fetchErrorInfo.code : 'n/a'),
    'Message: ' + (fetchErrorInfo ? fetchErrorInfo.message : 'n/a'),
    'Hint: ' + (fetchErrorInfo ? fetchErrorInfo.hint : 'n/a'),
    'Storage: ' + backend,
    'Revision: ' + lastRevision,
    'User agent: ' + navigator.userAgent,
  ];
  if (fetchErrorInfo && fetchErrorInfo.details && fetchErrorInfo.details.length > 1) {
    lines.push('Details:');
    for (const d of fetchErrorInfo.details) {
      lines.push(`- ${d.ticker}: [${d.code || 'upstream'}] ${d.message}`);
    }
  }
  return lines.join('\n');
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      /* Permission denied: fall through to the legacy copy path. */
    }
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(area);
  return ok;
}

async function retryFailedFetch() {
  const tickers = [...fetchErrorTickers];
  if (!tickers.length) return;
  el.errorRetry.disabled = true;
  el.errorRetry.textContent = 'Retrying…';
  const results = await Promise.allSettled(tickers.map(t => refreshTicker(t)));
  const failures = results
    .filter(r => r.status === 'fulfilled' && r.value && !r.value.ok)
    .map(r => r.value);
  if (failures.length) {
    reportFetchErrors(failures);
  } else {
    hideFetchError();
    showFormMessage('Data refreshed.', 'ok');
  }
  el.errorRetry.disabled = false;
  el.errorRetry.textContent = 'Retry';
}

async function fetchPortfolio() {
  const res = await fetch('/api/portfolio', { cache: 'no-store' });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
  return {
    holdings: Array.isArray(json.holdings) ? json.holdings : [],
    revision: Number(json.revision) || 0,
  };
}

function enqueueTickerOp(ticker, op) {
  const previous = saveQueues.get(ticker) || Promise.resolve();
  const next = previous.catch(() => {}).then(op);
  saveQueues.set(ticker, next);
  const cleanup = () => {
    if (saveQueues.get(ticker) === next) saveQueues.delete(ticker);
  };
  next.then(cleanup, cleanup);
  return next;
}

function dbSaveHolding(h, quiet) {
  if (deletedTickers.has(h.ticker)) return Promise.resolve(false);
  return enqueueTickerOp(h.ticker, () => dbSaveHoldingNow(h, quiet));
}

async function dbSaveHoldingNow(h, quiet) {
  // A delete may have happened while this save waited in the queue.
  if (deletedTickers.has(h.ticker)) return false;
  if (!quiet) setDbStatus('saving', 'DB: saving…', 'Writing to local ChromaDB…');
  try {
    const res = await fetch('/api/portfolio/' + encodeURIComponent(h.ticker), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(h),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    if (json.ignored) {
      // The server has a tombstone for this ticker: another window
      // deleted it. Mirror that instead of keeping a ghost row.
      deletedTickers.set(h.ticker, Date.now());
      syncedTickers.delete(h.ticker);
      saveSyncedTickers();
      if (getHolding(h.ticker) === h) {
        state.holdings = state.holdings.filter(x => x.ticker !== h.ticker);
        saveHoldings();
        render();
      }
      setDbStatus('saved', 'DB: synced', 'A stale copy was ignored because this holding was deleted elsewhere.');
      return false;
    }
    const count = json.storage ? json.storage.holdings : '?';
    const rev = typeof json.revision === 'number' ? json.revision : '?';
    syncedTickers.add(h.ticker);
    saveSyncedTickers();
    setDbStatus('saved', 'DB: saved', 'Saved in local ChromaDB (' + count + ' holdings · revision ' + rev + ')');
    return true;
  } catch (e) {
    setDbStatus('error', 'DB: local only', 'ChromaDB write failed: ' + (e.message || e) + '. Data is still saved in this browser.');
    return false;
  }
}

function dbDeleteHolding(ticker) {
  return enqueueTickerOp(ticker, () => dbDeleteHoldingNow(ticker));
}

async function dbDeleteHoldingNow(ticker) {
  // The user re-added this ticker before the queued delete ran.
  if (!deletedTickers.has(ticker)) return false;
  setDbStatus('saving', 'DB: saving…', 'Removing from local ChromaDB…');
  try {
    const res = await fetch('/api/portfolio/' + encodeURIComponent(ticker), { method: 'DELETE' });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    const rev = typeof json.revision === 'number' ? json.revision : '?';
    syncedTickers.delete(ticker);
    saveSyncedTickers();
    setDbStatus('saved', 'DB: saved', 'Saved in local ChromaDB (revision ' + rev + ')');
    return true;
  } catch (e) {
    // The row is still on the server. Drop the tombstone and pull the
    // real state back so the UI does not silently disagree with the DB.
    deletedTickers.delete(ticker);
    setDbStatus('error', 'DB: local only', 'ChromaDB delete failed: ' + (e.message || e));
    try {
      const portfolio = await fetchPortfolio();
      applyRemote(portfolio.holdings, portfolio.revision);
    } catch (_) {}
    return false;
  }
}

async function dbImportAll(items) {
  setDbStatus('saving', 'DB: saving…', 'Syncing portfolio to local ChromaDB…');
  try {
    const res = await fetch('/api/portfolio/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ holdings: items }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    const count = json.storage ? json.storage.holdings : items.length;
    const rev = typeof json.revision === 'number' ? json.revision : '?';
    setDbStatus('saved', 'DB: saved', 'Saved in local ChromaDB (' + count + ' holdings · revision ' + rev + ')');
    return true;
  } catch (e) {
    setDbStatus('error', 'DB: local only', 'ChromaDB sync failed: ' + (e.message || e) + '. Data is still saved in this browser.');
    return false;
  }
}

function scheduleSync(h) {
  if (syncTimers[h.ticker]) clearTimeout(syncTimers[h.ticker]);
  syncTimers[h.ticker] = setTimeout(() => {
    delete syncTimers[h.ticker];
    dbSaveHolding(h, true);
  }, 450);
}

function mergeHoldings(local, remote, knownRemote) {
  const map = new Map();
  for (const h of remote) {
    normalizeHolding(h);
    const deletedAt = deletedTickers.get(h.ticker);
    // Ignore a stale copy that predates the local delete. A holding
    // re-added elsewhere after the delete has a newer timestamp and wins.
    if (deletedAt) {
      if (timeValue(h.updatedAt) <= deletedAt) continue;
      deletedTickers.delete(h.ticker);
    }
    map.set(h.ticker, h);
  }
  for (const h of local) {
    normalizeHolding(h);
    const r = map.get(h.ticker);
    if (!r) {
      if (deletedTickers.has(h.ticker)) continue;
      // It was in the previous server snapshot but is gone now: another
      // window deleted it. Keep it only while a save is still pending.
      if (knownRemote && knownRemote.has(h.ticker) && !saveQueues.has(h.ticker) && !syncTimers[h.ticker]) continue;
      map.set(h.ticker, h);
      continue;
    }

    const localTime = timeValue(h.updatedAt);
    const remoteTime = timeValue(r.updatedAt);
    const winner = localTime > remoteTime ? h : r;
    const loser = winner === h ? r : h;

    const quarters = {};
    const quarterUpdatedAt = {};
    const keys = new Set([...Object.keys(h.quarters || {}), ...Object.keys(r.quarters || {})]);
    for (const key of keys) {
      const lt = quarterTime(h, key);
      const rt = quarterTime(r, key);
      let picked;
      if (lt !== null && rt !== null) picked = lt === rt ? winner : (lt > rt ? h : r);
      else if (lt !== null) picked = h;
      else if (rt !== null) picked = r;
      else picked = (winner.quarters && key in winner.quarters) ? winner : loser;

      if (picked.quarters && key in picked.quarters) quarters[key] = picked.quarters[key];
      else if (winner.quarters && key in winner.quarters) quarters[key] = winner.quarters[key];
      else if (loser.quarters && key in loser.quarters) quarters[key] = loser.quarters[key];

      if (lt !== null || rt !== null) {
        if (lt !== null && (rt === null || lt >= rt)) {
          quarterUpdatedAt[key] = h.quarterUpdatedAt[key];
        } else {
          quarterUpdatedAt[key] = r.quarterUpdatedAt[key];
        }
      }
    }

    const merged = {
      ...loser,
      ...winner,
      quarters,
      quarterUpdatedAt,
      dividends: (winner.dividends && winner.dividends.length) ? winner.dividends : (loser.dividends || []),
    };
    merged.yearTotals = computeYearTotals(merged.dividends);
    map.set(h.ticker, merged);
  }
  return [...map.values()].sort((a, b) => a.ticker.localeCompare(b.ticker));
}

function localChangesToPush(local, remote, knownRemote) {
  const remoteMap = new Map(remote.map(h => [h.ticker, h]));
  return local.filter(h => {
    if (deletedTickers.has(h.ticker)) return false;
    const r = remoteMap.get(h.ticker);
    if (!r) {
      // Never resurrect a holding that the server has already dropped.
      if (knownRemote && knownRemote.has(h.ticker)) return false;
      return true;
    }
    if (timeValue(h.updatedAt) > timeValue(r.updatedAt)) return true;
    return Object.keys(h.quarterUpdatedAt || {}).some(key => {
      const lt = timeValue(h.quarterUpdatedAt[key]);
      const rt = quarterTime(r, key);
      return lt > (rt === null ? 0 : rt);
    });
  });
}

function isEditingQuantity() {
  const active = document.activeElement;
  return !!(active && active.classList && active.classList.contains('qty-input'));
}

function applyRemote(remote, revision) {
  if (isEditingQuantity()) {
    pendingRemote = { remote, revision };
    return;
  }
  const before = state.holdings;
  const knownRemote = syncedTickers;
  const push = localChangesToPush(before, remote, knownRemote);
  state.holdings = mergeHoldings(before, remote, knownRemote);
  syncedTickers = new Set(remote.map(h => h.ticker));
  saveSyncedTickers();
  saveHoldings();
  render();
  lastRevision = revision;
  setDbStatus('saved', 'DB: synced', 'Synced with local ChromaDB (revision ' + revision + ')');
  for (const h of push) dbSaveHolding(h, true);
  if (state.analysisOpen) loadAnalysis();
  if (state.insightOpen) loadInsight();
}

function checkCalendarRollover() {
  const currentYear = new Date().getFullYear();
  if (currentYear === lastCalendarYear) return;
  // Do not re-render while a quantity input is focused: it would steal
  // focus mid-edit. The next poll will retry once the user is done.
  if (isEditingQuantity()) return;
  const wasViewingPreviousYear = state.year === lastCalendarYear;
  lastCalendarYear = currentYear;
  if (wasViewingPreviousYear || state.year === null) {
    // Move to the new year automatically. Stored quantities for earlier
    // years are never touched; the new year inherits the quantity held in
    // the previous year, and only quarters that were actually recorded.
    state.year = currentYear;
  }
  inheritQuantitiesForNewYear(currentYear);
  render();
}

// A year with no stored quarters starts blank and contributes zero income,
// except the year that just rolled over, which inherits the quantity held
// in the previous year (2027 inherits from 2026, 2028 from 2027, ...).
function inheritQuantitiesForNewYear(newYear) {
  const sourceYear = String(Number(newYear) - 1);
  for (const holding of state.holdings) {
    if (!holding.quarters) holding.quarters = {};
    for (let q = 1; q <= 4; q++) {
      const sourceKey = sourceYear + '-Q' + q;
      const targetKey = newYear + '-Q' + q;
      const source = holding.quarters[sourceKey];
      const valid = source !== undefined && source !== null && source !== '' && isFinite(Number(source)) && Number(source) >= 0;
      if (valid) holding.quarters[targetKey] = source;
    }
  }
}

async function pollNow() {
  if (!syncReady || pollInFlight) return;
  checkCalendarRollover();
  pollInFlight = true;
  try {
    const res = await fetch('/api/revision', { cache: 'no-store' });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    const revision = Number(json.revision) || 0;
    if (lastRevision === null || revision !== lastRevision) {
      const portfolio = await fetchPortfolio();
      if (isEditingQuantity()) {
        pendingRemote = { remote: portfolio.holdings, revision: portfolio.revision };
      } else {
        applyRemote(portfolio.holdings, portfolio.revision);
      }
    }
  } catch (e) {
    if (navigator.onLine === false) {
      setDbStatus('error', 'DB: offline — local cache', 'ChromaDB unavailable: ' + (e.message || e));
    }
  } finally {
    pollInFlight = false;
  }
}

function schedulePoll(delay) {
  if (pollDebounce) clearTimeout(pollDebounce);
  pollDebounce = setTimeout(() => {
    pollDebounce = null;
    pollNow();
  }, delay === undefined ? 250 : delay);
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollNow, POLL_MS);
}

// ---------- quarter helpers ----------
function quarterKey(date) {
  const s = String(date);
  const y = Number(s.slice(0, 4));
  const m = Number(s.slice(5, 7));
  if (!y || !m) return s;
  return y + '-Q' + (Math.floor((m - 1) / 3) + 1);
}

function computeYearTotals(dividends) {
  const totals = {};
  for (const d of dividends || []) {
    const year = String(d.date || '').slice(0, 4);
    if (!/^\d{4}$/.test(year)) continue;
    totals[year] = (totals[year] || 0) + (Number(d.amount) || 0);
  }
  for (const year of Object.keys(totals)) totals[year] = Math.round(totals[year] * 10000) / 10000;
  return totals;
}

function normalizeHolding(h) {
  h.quarters = h.quarters || {};
  h.quarterUpdatedAt = h.quarterUpdatedAt || {};
  h.shares = Math.max(0, Number(h.shares) || 1);
  const divs = [...(h.dividends || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  // Do NOT invent quantities for missing quarters or new years. A
  // quarter is independent: if no value is stored for 2027-Q1, it stays
  // blank (and contributes zero income) until the user enters it.
  h.yearTotals = computeYearTotals(divs);
  return h;
}

function isFyYear(year) {
  return String(year).startsWith('fy:');
}

function fyStartFrom(year) {
  return parseInt(String(year).split(':')[1], 10);
}

function dateInFy(date, start) {
  const y = parseInt(String(date).slice(0, 4), 10);
  const m = parseInt(String(date).slice(5, 7), 10);
  if (m >= 4) return y === start;
  return y === start + 1;
}

function fyLabel(start) {
  const end = start + 1;
  return `FY ${start}-${String(end).slice(-2)}`;
}

function fyFilterOptions() {
  const years = availableYears(true);
  const anchor = years.length ? Math.max(...years) : new Date().getFullYear();
  const out = [];
  for (let start = anchor; start >= anchor - 6; start--) {
    out.push({ value: `fy:${start}`, label: fyLabel(start) });
  }
  return out;
}

function analysisYearParam(year) {
  if (year === null || year === undefined) return 'all';
  return encodeURIComponent(String(year));
}

function availableYears(includeCurrent) {
  const set = new Set();
  state.holdings.forEach(h => (h.dividends || []).forEach(d => {
    const y = Number(String(d.date).slice(0, 4));
    if (y) set.add(y);
  }));
  if (includeCurrent) {
    // Only the current calendar year is added automatically. The next
    // year is deliberately not shown early: it appears on 1 January,
    // when new Date().getFullYear() rolls over. A future year that has
    // real dividend data still appears through the loop above.
    set.add(new Date().getFullYear());
  }
  return [...set].sort((a, b) => b - a);
}

function qtyForQuarter(h, key) {
  const raw = h.quarters ? h.quarters[key] : undefined;
  if (raw !== undefined && raw !== null && raw !== '') {
    const n = Number(raw);
    if (isFinite(n) && n >= 0) return n;
  }
  // Missing means "not recorded", not "same as last quarter" and not
  // "same as 2026". It contributes zero income until explicitly set.
  return 0;
}

function incomeFor(h, year) {
  return (h.dividends || []).reduce((sum, d) => {
    if (isFyYear(year)) {
      if (!dateInFy(d.date, fyStartFrom(year))) return sum;
    } else {
      const y = Number(String(d.date).slice(0, 4));
      if (year !== 'all' && y !== year) return sum;
    }
    return sum + (Number(d.amount) || 0) * qtyForQuarter(h, quarterKey(d.date));
  }, 0);
}

// TOTAL DIVIDEND = per-share dividend paid in Q1+Q2+Q3+Q4 of the year.
// It is deliberately independent of the quantity held; ANNUAL INCOME
// applies those quantities to the same payouts.
function totalDividendFor(h, year) {
  if (year === 'all') {
    return (h.dividends || []).reduce((sum, d) => sum + (Number(d.amount) || 0), 0);
  }
  if (isFyYear(year)) {
    const start = fyStartFrom(year);
    return (h.dividends || []).reduce((sum, d) => {
      if (!dateInFy(d.date, start)) return sum;
      return sum + (Number(d.amount) || 0);
    }, 0);
  }
  const key = String(year);
  if (h.yearTotals && h.yearTotals[key] !== undefined) return Number(h.yearTotals[key]) || 0;
  return (h.dividends || []).reduce((sum, d) => {
    if (String(d.date).slice(0, 4) !== key) return sum;
    return sum + (Number(d.amount) || 0);
  }, 0);
}

function maxQuarterQtyFor(h, year) {
  if (year === 'all') return 0;
  return Math.max(...[1, 2, 3, 4].map(q => qtyForQuarter(h, `${year}-Q${q}`)));
}

// PROJECTED NEXT YEAR INCOME assumes the largest quantity held in any
// quarter of `year` is held for all of next year, and the same total
// dividend per share is paid again: Σ max(Q1..Q4) × total dividend.
function projectedIncomeFor(h, year) {
  if (year === 'all') return 0;
  return maxQuarterQtyFor(h, year) * totalDividendFor(h, year);
}

function renderProjectionScenarios() {
  const row = el.projectionScenarios;
  const projections = state.summaryProjections;
  if (!row) return;
  if (!projections || state.year === 'all') {
    row.hidden = true;
    row.innerHTML = '';
    return;
  }
  const bands = [
    { key: 'conservative', label: 'Low' },
    { key: 'base', label: 'Base' },
    { key: 'growth', label: 'Growth' },
  ];
  const note = (projections.notes || []).join(' ');
  row.title = note;
  row.hidden = false;
  row.innerHTML = bands.map(b => {
    const band = projections[b.key];
    if (!band || band.income === null || band.income === undefined) return '';
    return `<span title="${esc(projections.notes?.[bands.indexOf(b)] || '')}">${b.label}: <strong>${esc(fmtMoney(band.income))}</strong></span>`;
  }).join('');
}

function renderIncomeBridge(bridge) {
  if (!el.incomeBridge) return;
  if (!bridge || bridge.delta === null || bridge.delta === undefined) {
    el.incomeBridge.hidden = true;
    el.incomeBridge.innerHTML = '';
    return;
  }
  const pct = bridge.deltaPct;
  const sign = bridge.delta >= 0 ? '+' : '';
  const pills = (bridge.components || []).map(c => {
    const amt = Number(c.amount) || 0;
    const s = amt >= 0 ? '+' : '';
    return `<span class="bridge-pill" title="${esc(c.label)}">${esc(c.label)}: ${s}${fmtMoney(amt)}</span>`;
  }).join('');
  el.incomeBridge.hidden = false;
  el.incomeBridge.innerHTML =
    `<strong>${esc(String(bridge.previousYear))} → ${esc(String(bridge.currentYear))}</strong>: ` +
    `${fmtMoney(bridge.previousIncome)} → ${fmtMoney(bridge.currentIncome)} ` +
    `(${sign}${fmtMoney(bridge.delta)}${pct !== null && pct !== undefined ? `, ${sign}${pct.toFixed(1)}%` : ''}).` +
    `<div class="bridge-pills">${pills}</div>`;
}

function growthForTicker(ticker) {
  const table = state.summaryAnalysis && state.summaryAnalysis.growthByTicker;
  return table ? table[ticker] : null;
}

function dpsYoyFor(h, year) {
  const row = growthForTicker(h.ticker);
  if (row && row.dpsYoyPct !== null && row.dpsYoyPct !== undefined) return row.dpsYoyPct;
  const totals = h.yearTotals || {};
  const curr = Number(totals[year]) || 0;
  const prev = Number(totals[year - 1]) || 0;
  if (prev <= 0 || curr <= 0) return null;
  return ((curr - prev) / prev) * 100;
}

function dpsTrendFor(h) {
  const row = growthForTicker(h.ticker);
  if (row && row.dpsTrend && row.dpsTrend.length) return row.dpsTrend;
  const totals = h.yearTotals || {};
  return Object.keys(totals).map(y => ({ year: Number(y), amount: Number(totals[y]) }))
    .filter(p => p.amount > 0).sort((a, b) => a.year - b.year).slice(-5);
}

function dpsSparkline(trend) {
  if (!trend || !trend.length) return '';
  const max = Math.max(...trend.map(p => Number(p.amount) || 0), 0.0001);
  const bars = trend.map(p => {
    const h = Math.max(2, Math.round(((Number(p.amount) || 0) / max) * 16));
    return `<span style="height:${h}px" title="${p.year}: ₹${Number(p.amount).toFixed(2)}/sh"></span>`;
  }).join('');
  return `<span class="dps-spark" aria-hidden="true">${bars}</span>`;
}

async function loadSummaryProjections() {
  const year = state.year;
  if (year === 'all' || year === null || String(year).startsWith('fy:')) {
    state.summaryProjections = null;
    state.summaryAnalysis = null;
    renderProjectionScenarios();
    renderIncomeBridge(null);
    return;
  }
  const token = ++summaryProjectionToken;
  try {
    const res = await fetch('/api/analysis?year=' + analysisYearParam(year));
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    if (token !== summaryProjectionToken) return;
    state.summaryAnalysis = json;
    state.summaryProjections = json.projections || null;
    if (json.projectedNextYearIncome !== null && json.projectedNextYearIncome !== undefined) {
      el.sumProjected.textContent = fmtMoney(json.projectedNextYearIncome);
    }
    renderProjectionScenarios();
    renderIncomeBridge(json.incomeBridge || null);
    renderTable();
  } catch (e) {
    if (token !== summaryProjectionToken) return;
    state.summaryProjections = null;
    state.summaryAnalysis = null;
    renderProjectionScenarios();
    renderIncomeBridge(null);
  }
}

function ttmPerShare(h) {
  const cutoff = new Date(); cutoff.setFullYear(cutoff.getFullYear() - 1);
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return (h.dividends || []).filter(d => String(d.date) >= cutoffStr)
    .reduce((s, d) => s + (Number(d.amount) || 0), 0);
}

function yieldFor(h) {
  const ttm = ttmPerShare(h);
  return (h.price && ttm > 0) ? (ttm / Number(h.price)) * 100 : null;
}

// ---------- data fetch ----------
async function fetchDividends(ticker) {
  const res = await fetch('/api/dividends?symbol=' + encodeURIComponent(ticker));
  let json = {};
  try { json = await res.json(); } catch (e) { /* non-JSON */ }
  if (!res.ok) {
    const err = new Error(json.error || ('Request failed with status ' + res.status));
    err.code = json.code || ('http_' + res.status);
    err.hint = json.hint || '';
    err.symbol = json.symbol || ticker;
    err.status = res.status;
    throw err;
  }
  const dividends = (json.dividends || [])
    .map(d => ({ date: d.date, amount: Number(d.amount) || 0 }))
    .filter(d => d.date && d.amount > 0)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return { name: json.name || ticker, price: json.price ?? null, dividends };
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    const out = {};
    for (const key of Object.keys(value).sort()) out[key] = stableValue(value[key]);
    return out;
  }
  return value;
}

function sameJson(a, b) {
  return JSON.stringify(stableValue(a === undefined ? null : a))
    === JSON.stringify(stableValue(b === undefined ? null : b));
}

function holdingStillCurrent(ticker, holding) {
  return !deletedTickers.has(ticker) && getHolding(ticker) === holding;
}

async function refreshTicker(ticker) {
  let holding = getHolding(ticker);
  if (!holding || deletedTickers.has(ticker)) return null;
  const previousStatus = holding.status;
  const previousError = holding.lastError;
  let changed = false;
  let result;
  try {
    const data = await fetchDividends(ticker);
    // The row may have been deleted while the request was in flight.
    if (!holdingStillCurrent(ticker, holding)) return null;
    if (data.name && data.name !== holding.name) { holding.name = data.name; changed = true; }
    if (data.price !== holding.price) { holding.price = data.price; changed = true; }
    if (!sameJson(data.dividends, holding.dividends)) { holding.dividends = data.dividends; changed = true; }
    normalizeHolding(holding);
    // The add form's default quantity is applied only once, when the
    // stock is first added. Future quarters (for example 2027) are never
    // seeded from an older quarter or year.
    const defaultQty = pendingDefaults[ticker];
    if (defaultQty !== undefined) {
      for (const d of holding.dividends || []) {
        const key = quarterKey(d.date);
        if (holding.quarters[key] === undefined) holding.quarters[key] = defaultQty;
      }
      delete pendingDefaults[ticker];
      changed = true;
    }
    if (previousStatus !== 'ok' || previousError) changed = true;
    holding.status = 'ok';
    holding.lastError = null;
    holding.lastErrorHint = null;
    holding.lastErrorCode = null;
    if (changed) {
      holding.lastFetched = new Date().toISOString();
      touch(holding);
    }
    result = { ticker, ok: true };
  } catch (e) {
    if (!holdingStillCurrent(ticker, holding)) return null;
    const message = String((e && e.message) || e);
    const code = (e && e.code) || ((e instanceof TypeError) ? 'network' : 'upstream');
    const hint = (e && e.hint) || (ERROR_FALLBACKS[code] ? ERROR_FALLBACKS[code].hint : '');
    if (previousStatus !== 'error' || previousError !== message) changed = true;
    holding.status = 'error';
    holding.lastError = message;
    holding.lastErrorHint = hint;
    holding.lastErrorCode = code;
    if (changed) touch(holding);
    result = { ticker, ok: false, code, message, hint };
  }
  if (!holdingStillCurrent(ticker, holding)) return null;
  render();
  if (changed) await dbSaveHolding(holding, true);
  return result;
}

async function refreshAll() {
  if (refreshing) return [];
  refreshing = true;
  const results = await Promise.allSettled(state.holdings.map(h => refreshTicker(h.ticker)));
  refreshing = false;
  persist();
  const failures = results
    .filter(r => r.status === 'fulfilled' && r.value && !r.value.ok)
    .map(r => r.value);
  if (failures.length) reportFetchErrors(failures);
  else if (!el.errorBanner.hidden) hideFetchError();
  return failures;
}

async function addTickers(raw, shares) {
  const parts = String(raw || '').split(/[\s,;]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
  const normalized = parts.map(t => t.replace(/\.NS$/, ''));
  const tickerRe = /^(?=.{2,15}$)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$/;
  const valid = [...new Set(normalized.filter(t => tickerRe.test(t)))];
  const invalid = [...new Set(normalized.filter(t => !tickerRe.test(t)))];
  if (!valid.length) {
    showFormMessage(
      'No valid NSE tickers found' + (invalid.length ? ': ' + invalid.join(', ') : '') +
      '. Use 2–15 letters or digits, e.g. RELIANCE or BAJAJ-AUTO.',
      'error'
    );
    return;
  }
  const addedHoldings = [];
  for (const t of valid) {
    if (getHolding(t)) continue;
    // Re-adding a ticker clears its tombstone so it can sync again.
    deletedTickers.delete(t);
    const qty = Math.max(0, Number(shares) || 1);
    const holding = {
      ticker: t, name: t, shares: qty,
      quarters: {}, dividends: [], price: null,
      status: 'pending', lastError: null, lastFetched: null,
    };
    pendingDefaults[t] = qty;
    normalizeHolding(holding);
    touch(holding);
    state.holdings.push(holding);
    addedHoldings.push(holding);
  }
  const skipped = invalid.length ? ' Skipped invalid: ' + invalid.join(', ') + '.' : '';
  showFormMessage(
    (addedHoldings.length ? 'Adding ' + valid.join(', ') + '…' : 'Already in your list.') + skipped,
    invalid.length ? 'error' : 'info'
  );
  if (addedHoldings.length) {
    persist();
    await Promise.all(addedHoldings.map(h => dbSaveHolding(h, true)));
    refreshAll();
  }
}

// ---------- bulk import ----------
// Some sheets use a company shorthand instead of the exact NSE symbol.
const IMPORT_ALIASES = { JYOTIRESINS: 'JYOTIRES' };

function cleanImportCell(value) {
  return String(value === undefined || value === null ? '' : value)
    .replace(/^\uFEFF/, '')
    .trim()
    .replace(/^"(.*)"$/, '$1')
    .trim();
}

function parseImportQty(value) {
  const raw = cleanImportCell(value).replace(/,/g, '').replace(/[^0-9.\-]/g, '');
  if (!raw || raw === '-' || raw === '.') return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.floor(n);
}

function parseImportRows(text) {
  const rows = [];
  const errors = [];
  const lines = String(text || '').split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    let cells;
    if (line.includes('\t')) cells = line.split('\t');
    else if (line.includes(',')) cells = line.split(',');
    else cells = line.split(/\s+/);
    cells = cells.map(cleanImportCell);
    const first = cells[0] || '';
    if (/^(share\s*name|share|stock|stock\s*name|ticker|symbol|name)$/i.test(first)) continue;
    const rawTicker = first.toUpperCase().replace(/\.NS$/, '').replace(/\s+/g, '');
    if (!/^(?=.{2,15}$)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$/.test(rawTicker)) {
      errors.push({ line: i + 1, reason: `invalid ticker "${first}"` });
      continue;
    }
    const ticker = IMPORT_ALIASES[rawTicker] || rawTicker;
    const qtys = [1, 2, 3, 4].map(q => parseImportQty(cells[q]));
    rows.push({ ticker, rawTicker, qtys, line: i + 1 });
  }
  return { rows, errors };
}

function renderImportYears() {
  const years = availableYears(true);
  el.importYear.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join('');
  const preferred = state.year !== 'all' && years.includes(state.year)
    ? state.year
    : new Date().getFullYear();
  el.importYear.value = String(years.includes(preferred) ? preferred : years[0]);
}

function openImport() {
  renderImportYears();
  el.importText.value = '';
  el.importFile.value = '';
  el.importMsg.textContent = '';
  el.importResult.innerHTML = '';
  el.importModal.hidden = false;
  el.importText.focus();
}

function closeImport() { el.importModal.hidden = true; }

async function refreshImportedHoldings(holdings) {
  const failures = [];
  await Promise.allSettled(holdings.map(async h => {
    try {
      const data = await fetchDividends(h.ticker);
      h.name = data.name || h.name;
      h.price = data.price;
      h.dividends = data.dividends;
      h.status = 'ok';
      h.lastError = null;
      h.lastErrorHint = null;
      h.lastErrorCode = null;
    } catch (e) {
      const message = String((e && e.message) || e);
      const code = (e && e.code) || ((e instanceof TypeError) ? 'network' : 'upstream');
      const hint = (e && e.hint) || (ERROR_FALLBACKS[code] ? ERROR_FALLBACKS[code].hint : '');
      h.status = 'error';
      h.lastError = message;
      h.lastErrorHint = hint;
      h.lastErrorCode = code;
      failures.push({ ticker: h.ticker, ok: false, code, message, hint });
    }
    h.lastFetched = new Date().toISOString();
    normalizeHolding(h);
    touch(h);
  }));
  if (failures.length) reportFetchErrors(failures);
  persist();
  await dbImportAll(holdings);
}

function renderImportResult(result) {
  const { year, added, skipped, invalid, corrected } = result;
  const parts = [];
  if (added.length) {
    const failed = added.filter(h => h.status === 'error');
    const withData = added.length - failed.length;
    parts.push(
      `<div class="ok">Added ${added.length} stock${added.length === 1 ? '' : 's'} to ${year}` +
      `${withData < added.length ? ` (${withData} with dividend data)` : ''}: ` +
      `${added.map(h => esc(h.ticker)).join(', ')}</div>`
    );
    if (failed.length) {
      parts.push(`<div class="warn">Quantities saved, but no dividend history was found for: ${failed.map(h => esc(h.ticker)).join(', ')}. Use 🔄 Refresh later.</div>`);
    }
  }
  if (corrected && corrected.length) {
    parts.push(`<div class="note">Ticker corrected: ${corrected.map(c => esc(c.from) + ' → ' + esc(c.to)).join(', ')}.</div>`);
  }
  if (skipped.length) {
    const groups = new Map();
    for (const item of skipped) {
      if (!groups.has(item.reason)) groups.set(item.reason, []);
      groups.get(item.reason).push(item.ticker);
    }
    for (const [reason, tickers] of groups) {
      parts.push(`<div class="warn">Skipped ${tickers.length} ${esc(reason)}: ${tickers.map(esc).join(', ')}</div>`);
    }
  }
  if (invalid.length) {
    parts.push(`<div class="err">${invalid.length} invalid row${invalid.length === 1 ? '' : 's'}: ${invalid.map(e => 'line ' + e.line + ' — ' + esc(e.reason)).join('; ')}</div>`);
  }
  el.importResult.innerHTML = parts.join('') || '<div class="note">Nothing imported.</div>';
}

async function runImport() {
  const year = Number(el.importYear.value);
  const { rows, errors } = parseImportRows(el.importText.value);
  if (!rows.length) {
    el.importMsg.textContent = errors.length
      ? 'No valid rows found. Check the first column and the quantities.'
      : 'Paste at least one row first.';
    el.importResult.innerHTML = '';
    return;
  }

  // Check both the open page and ChromaDB so a row is discarded even if
  // another window added it while this modal was open.
  const existing = new Set(state.holdings.map(h => h.ticker));
  try {
    const portfolio = await fetchPortfolio();
    for (const h of portfolio.holdings) existing.add(h.ticker);
  } catch (e) { /* fall back to the local list */ }

  const seen = new Set();
  const added = [];
  const skipped = [];
  const corrected = [];
  const invalid = [...errors];

  for (const row of rows) {
    if (existing.has(row.ticker)) {
      skipped.push({ ticker: row.ticker, reason: 'already in the tracker' });
      continue;
    }
    if (seen.has(row.ticker)) {
      skipped.push({ ticker: row.ticker, reason: 'duplicate rows in the pasted data' });
      continue;
    }
    seen.add(row.ticker);
    if (row.rawTicker !== row.ticker) corrected.push({ from: row.rawTicker, to: row.ticker });

    const quantities = row.qtys.filter(v => v !== null);
    const holding = {
      ticker: row.ticker,
      name: row.ticker,
      shares: quantities.length ? Math.max(...quantities) : 1,
      quarters: {},
      dividends: [],
      price: null,
      status: 'pending',
      lastError: null,
      lastFetched: null,
    };
    row.qtys.forEach((q, idx) => {
      if (q === null) return;
      holding.quarters[`${year}-Q${idx + 1}`] = q;
    });
    normalizeHolding(holding);
    touch(holding);
    for (const key of Object.keys(holding.quarters)) touch(holding, key);
    deletedTickers.delete(row.ticker);
    state.holdings.push(holding);
    added.push(holding);
  }

  if (!added.length) {
    el.importMsg.textContent = 'Nothing to add — every valid row is already in the tracker or duplicated.';
    renderImportResult({ year, added, skipped, invalid, corrected });
    return;
  }

  el.importRun.disabled = true;
  el.importMsg.textContent = `Adding ${added.length} stock${added.length === 1 ? '' : 's'} to ${year}…`;
  persist();
  const dbOk = await dbImportAll(added);
  el.importMsg.textContent = `Saved ${added.length} stock${added.length === 1 ? '' : 's'}. Fetching dividend history…`;
  await refreshImportedHoldings(added);
  el.importMsg.textContent = dbOk
    ? 'Import complete.'
    : 'Imported in this browser, but the ChromaDB save failed — see the DB status.';
  el.importRun.disabled = false;
  renderImportResult({ year, added, skipped, invalid, corrected });
}

// ---------- formatting ----------
function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmtMoney(n) {
  const v = Number(n);
  if (n === null || n === undefined || isNaN(v)) return '—';
  return v.toLocaleString('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtPct(n) { const v = Number(n); if (n === null || n === undefined || isNaN(v)) return '—'; return v.toFixed(2) + '%'; }
function fmtDate(d) {
  const dt = new Date(String(d) + 'T00:00:00');
  if (isNaN(dt.getTime())) return String(d || '—');
  return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}
function statusTitle(h) {
  if (h.status === 'ok') return 'Live data';
  if (h.status === 'pending') return 'Fetching…';
  const base = h.lastError || 'Fetch failed';
  return h.lastErrorHint ? base + ' — ' + h.lastErrorHint : base;
}

// ---------- rendering ----------
function renderYearFilter() {
  const years = availableYears(true);
  const dividendYears = availableYears(false);
  const currentYear = new Date().getFullYear();
  let value = state.year;
  if (value === null || (value !== 'all' && !isFyYear(value) && !years.includes(value))) {
    // Open on the current calendar year. This is what makes the filter
    // move to 2027 automatically on 1 January 2027. If the current year
    // is not in the list for some reason, fall back to the latest year
    // that actually has dividends.
    value = years.includes(currentYear)
      ? currentYear
      : (dividendYears.length ? dividendYears[0] : 'all');
    state.year = value;
  }
  const fyOpts = fyFilterOptions();
  el.yearFilter.innerHTML = '<option value="all">All years</option>' +
    years.map(y => `<option value="${y}"${String(y) === String(value) ? ' selected' : ''}>${y}</option>`).join('') +
    fyOpts.map(f => `<option value="${f.value}"${String(f.value) === String(value) ? ' selected' : ''}>${f.label}</option>`).join('');
  el.yearFilter.value = String(value);
  el.clearYearBtn.disabled = value === 'all' || isFyYear(value);
  renderYearNote();
}

function renderYearNote() {
  const year = state.year;
  if (year === 'all' || isFyYear(year)) { el.yearNote.textContent = isFyYear(year) ? `${fyLabel(fyStartFrom(year))} uses April–March payout dates.` : ''; return; }
  const hasDividend = state.holdings.some(h => (h.dividends || []).some(d => Number(String(d.date).slice(0, 4)) === year));
  const hasQuantity = state.holdings.some(h => Object.keys(h.quarters || {}).some(k => k.startsWith(year + '-') && Number(h.quarters[k]) > 0));
  el.yearNote.textContent = (!hasDividend && !hasQuantity)
    ? `${year} has no dividends yet. Quarter quantities start blank; earlier years are unchanged.`
    : '';
}

function renderSummary() {
  const year = state.year;
  el.sumCount.textContent = state.holdings.length;
  el.sumIncomeLabel.textContent = year === 'all'
    ? 'Total income (all years)'
    : (isFyYear(year) ? fyLabel(fyStartFrom(year)) + ' income' : 'Annual income ' + year);
  const total = state.holdings.reduce((s, h) => s + incomeFor(h, year), 0);
  el.sumIncome.textContent = state.holdings.length ? fmtMoney(total) : '—';

  const projected = (year === 'all' || isFyYear(year))
    ? null
    : state.holdings.reduce((s, h) => s + projectedIncomeFor(h, year), 0);
  el.projectionCard.title = year === 'all'
    ? 'Select a specific year to project next year\'s income from its quarterly quantities and total dividends.'
    : `Projected ${Number(year) + 1} income = Σ (highest quantity held in any quarter of ${year} × total dividend per share paid in ${year}). Assumes the peak quantity is held all year and the same dividends repeat.`;
  el.sumProjected.textContent = (projected === null || !state.holdings.length) ? '—' : fmtMoney(projected);
  el.sumProjectedDelta.className = 'delta';
  if (projected === null) {
    el.sumProjectedDelta.textContent = 'Select a year';
  } else if (!state.holdings.length) {
    el.sumProjectedDelta.textContent = 'No holdings yet';
  } else if (!(total > 0)) {
    el.sumProjectedDelta.textContent = 'No income for ' + year;
  } else {
    const pct = ((projected - total) / total) * 100;
    const direction = pct > 0.005 ? 'up' : (pct < -0.005 ? 'down' : 'flat');
    if (direction !== 'flat') el.sumProjectedDelta.classList.add(direction);
    const arrow = direction === 'up' ? '▲ ' : (direction === 'down' ? '▼ ' : '');
    const sign = direction === 'up' ? '+' : (direction === 'down' ? '-' : '');
    el.sumProjectedDelta.textContent = `${arrow}${sign}${Math.abs(pct).toFixed(1)}% vs ${year}`;
  }
  renderProjectionScenarios();

  el.sumMonthlyLabel.textContent = year === 'all' ? 'Monthly income' : 'Monthly income ' + year;
  el.sumMonthly.textContent = state.holdings.length && year !== 'all' && !isFyYear(year) ? fmtMoney(total / 12) : '—';
  const yields = state.holdings.map(yieldFor).filter(y => y !== null);
  el.sumYield.textContent = yields.length ? (yields.reduce((a, b) => a + b, 0) / yields.length).toFixed(2) + '%' : '—';
}

function actionsCell(h) {
  return `<button class="icon-btn" data-action="history" data-ticker="${esc(h.ticker)}" title="View full dividend history for ${esc(h.ticker)}" aria-label="Dividend history for ${esc(h.ticker)}">📜</button>` +
         `<button class="icon-btn" data-action="refresh" data-ticker="${esc(h.ticker)}" title="Refresh ${esc(h.ticker)} dividend data from the web" aria-label="Refresh ${esc(h.ticker)}">🔄</button>` +
         `<button class="icon-btn" data-action="delete" data-ticker="${esc(h.ticker)}" title="Remove ${esc(h.ticker)} from your tracker" aria-label="Delete ${esc(h.ticker)}">🗑</button>`;
}

function totalDividendCell(h, year) {
  const total = totalDividendFor(h, year);
  const title = year === 'all'
    ? 'Total dividend per share across all recorded years (Q1+Q2+Q3+Q4 for each year) — independent of quantity'
    : `Total dividend per share paid in ${year} = Q1 + Q2 + Q3 + Q4 — independent of quantity; Annual income multiplies it by your quarterly quantities`;
  if (!(total > 0)) return `<td class="num muted" title="${esc(title)}">—</td>`;
  return `<td class="num dividend-cell" title="${esc(title)}">${fmtMoney(total)}<span class="per-share">/sh</span></td>`;
}

function rowFor(h, year) {
  const dot = `<span class="dot ${h.status}" title="${esc(statusTitle(h))}"></span>`;
  const errorFlag = h.status === 'error'
    ? `<span class="row-error" title="${esc(statusTitle(h))}" aria-label="Dividend refresh failed">⚠</span>`
    : '';
  const stockCell = `<td><div class="stock-cell">${dot}<span class="ticker-badge" title="${esc(h.ticker)} — ${esc(h.name)}">${esc(h.ticker)}</span><span class="stock-name" title="${esc(h.name)}">${esc(h.name)}</span>${errorFlag}</div></td>`;
  const actions = `<td class="num">${actionsCell(h)}</td>`;
  const yieldCell = `<td class="num yield-cell" title="Trailing 12-month dividend per share ÷ current price">${fmtPct(yieldFor(h))}</td>`;
  const growthRow = growthForTicker(h.ticker);
  const fyView = isFyYear(year);
  const yoy = year !== 'all' && year !== null && !String(year).startsWith('fy:') ? dpsYoyFor(h, year) : null;
  const cagr = growthRow && growthRow.dpsCagrPct !== null ? growthRow.dpsCagrPct : null;
  const growthCells = (year === 'all' || fyView) ? '' :
    `<td class="num" title="Year-on-year change in total dividend per share">${yoy === null ? '—' : (yoy >= 0 ? '+' : '') + yoy.toFixed(1) + '%'}</td>` +
    `<td class="num" title="5-year dividend per share CAGR">${dpsSparkline(dpsTrendFor(h))}${cagr === null ? '—' : cagr.toFixed(1) + '%'}</td>`;

  if (year === 'all' || isFyYear(year)) {
    return `<tr data-ticker="${esc(h.ticker)}">${stockCell}` +
           `<td class="num income-cell">${fmtMoney(incomeFor(h, year))}</td>` +
           `${totalDividendCell(h, year)}${yieldCell}${actions}</tr>`;
  }

  const qtyCells = [1, 2, 3, 4].map(q => {
    const key = year + '-Q' + q;
    const divs = (h.dividends || []).filter(d => quarterKey(d.date) === key);
    const perShare = divs.reduce((s, d) => s + (Number(d.amount) || 0), 0);
    const rawQty = h.quarters ? h.quarters[key] : undefined;
    const qty = (rawQty === undefined || rawQty === null || rawQty === '') ? '' : rawQty;
    const hint = divs.length
      ? `<span class="div-hint" title="Total dividend per share paid in ${key.replace('-', ' ')}">₹${perShare.toFixed(2)}/sh</span>`
      : `<span class="div-hint muted" title="No dividend recorded for ${key.replace('-', ' ')}">No dividend</span>`;
    return `<td><input class="qty-input" type="number" min="0" step="1" value="${esc(qty)}" placeholder="0" ` +
           `data-ticker="${esc(h.ticker)}" data-quarter="${key}" aria-label="Quantity held in ${key}" ` +
           `title="Quantity of ${esc(h.ticker)} held in ${key.replace('-', ' ')} — blank means 0; edit to set this quarter's income" />` +
           `${hint}</td>`;
  }).join('');

  return `<tr data-ticker="${esc(h.ticker)}">${stockCell}${qtyCells}` +
         `<td class="num income-cell">${fmtMoney(incomeFor(h, year))}</td>` +
         `${totalDividendCell(h, year)}${growthCells}${yieldCell}${actions}</tr>`;
}

function defaultSortDir(key) {
  return key === 'stock' ? 'asc' : 'desc';
}

function sortHeader(label, key, opts) {
  opts = opts || {};
  const active = state.sortKey === key;
  const dir = state.sortDir === 'asc' ? 'asc' : 'desc';
  const arrow = active ? (dir === 'asc' ? '▲' : '▼') : '↕';
  const classes = ['sortable'];
  if (opts.num) classes.push('num');
  if (active) classes.push('sorted');
  const ariaSort = active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';
  const title = (opts.title || label) + ' — click to sort; click again to reverse';
  return `<th class="${classes.join(' ')}" data-sort="${key}" tabindex="0" aria-sort="${ariaSort}" title="${esc(title)}">${esc(label)}<span class="sort-arrow" aria-hidden="true">${arrow}</span></th>`;
}

function sortHoldings(holdings, year) {
  const key = state.sortKey;
  if (!key) return holdings;
  // Quarter columns do not exist in the "all years" view.
  if ((year === 'all' || isFyYear(year)) && (key === 'q1' || key === 'q2' || key === 'q3' || key === 'q4')) {
    return holdings;
  }
  const dir = state.sortDir === 'desc' ? -1 : 1;
  const valueFor = (h) => {
    if (key === 'stock') return String(h.ticker || '');
    if (key === 'income') return incomeFor(h, year);
    if (key === 'totalDividend') return totalDividendFor(h, year);
    if (key === 'yield') {
      const value = yieldFor(h);
      return (value === null || Number.isNaN(Number(value))) ? null : Number(value);
    }
    if (key === 'dpsYoy') {
      const value = dpsYoyFor(h, year);
      return (value === null || Number.isNaN(Number(value))) ? null : Number(value);
    }
    if (key === 'dpsCagr') {
      const row = growthForTicker(h.ticker);
      const value = row ? row.dpsCagrPct : null;
      return (value === null || Number.isNaN(Number(value))) ? null : Number(value);
    }
    if (key === 'q1' || key === 'q2' || key === 'q3' || key === 'q4') {
      return qtyForQuarter(h, `${year}-Q${key.slice(1)}`);
    }
    return 0;
  };
  return [...holdings].sort((a, b) => {
    const av = valueFor(a);
    const bv = valueFor(b);
    // Unknown yields always sort last, regardless of direction.
    if (av === null && bv === null) return String(a.ticker).localeCompare(String(b.ticker));
    if (av === null) return 1;
    if (bv === null) return -1;
    let cmp;
    if (typeof av === 'string' || typeof bv === 'string') {
      cmp = String(av).localeCompare(String(bv), undefined, { sensitivity: 'base', numeric: true });
    } else {
      cmp = (Number(av) || 0) - (Number(bv) || 0);
    }
    if (cmp === 0) return String(a.ticker).localeCompare(String(b.ticker));
    return cmp * dir;
  });
}

function renderTable() {
  const year = state.year;
  const fyView = isFyYear(year);
  const all = year === 'all' || fyView;
  el.tableHead.innerHTML = all
    ? sortHeader('Stock', 'stock', { title: 'Stock symbol and company name' }) +
      sortHeader(fyView ? 'FY income' : 'Total income', 'income', { num: true, title: fyView ? 'Dividends received in the selected financial year' : 'Total dividends received across all years' }) +
      sortHeader('Total dividend', 'totalDividend', { num: true, title: fyView ? 'Total dividend per share paid in this financial year' : 'Total dividend per share across all recorded years = Q1+Q2+Q3+Q4 for each year' }) +
      sortHeader('Yield', 'yield', { num: true, title: 'Trailing 12-month dividend per share ÷ current price' }) +
      '<th></th>'
    : sortHeader('Stock', 'stock', { title: 'Stock symbol and company name' }) +
      sortHeader('Q1', 'q1', { num: true, title: 'Quantity held in January–March; edit to change this quarter\'s income' }) +
      sortHeader('Q2', 'q2', { num: true, title: 'Quantity held in April–June; edit to change this quarter\'s income' }) +
      sortHeader('Q3', 'q3', { num: true, title: 'Quantity held in July–September; edit to change this quarter\'s income' }) +
      sortHeader('Q4', 'q4', { num: true, title: 'Quantity held in October–December; edit to change this quarter\'s income' }) +
      sortHeader('Annual income', 'income', { num: true, title: 'Dividends received this year = Σ (dividend per share × quarter quantity)' }) +
      sortHeader('Total dividend', 'totalDividend', { num: true, title: 'Total dividend per share this year = Q1+Q2+Q3+Q4 — independent of quantity' }) +
      sortHeader('DPS YoY', 'dpsYoy', { num: true, title: 'Change in total dividend per share vs the previous year' }) +
      sortHeader('DPS CAGR', 'dpsCagr', { num: true, title: 'Compound growth of dividend per share (up to 5 years)' }) +
      sortHeader('Yield', 'yield', { num: true, title: 'Trailing 12-month dividend per share ÷ current price' }) +
      '<th></th>';

  const empty = !state.holdings.length;
  el.emptyState.hidden = !empty;
  el.tableWrap.hidden = empty;
  el.scrollHint.hidden = empty;
  if (empty) {
    el.holdingsBody.innerHTML = `<tr><td colspan="${all ? 5 : 11}" class="empty-cell">No stocks yet — add NSE tickers above (e.g. RELIANCE, TCS, HDFCBANK).</td></tr>`;
    return;
  }
  el.holdingsBody.innerHTML = sortHoldings(state.holdings, year).map(h => rowFor(h, year)).join('');
}

function renderHistory() {
  const h = state.historyTicker ? getHolding(state.historyTicker) : null;
  if (!h) { el.historyModal.hidden = true; return; }

  const divs = [...(h.dividends || [])].sort((a, b) => String(b.date).localeCompare(String(a.date)));
  const totalIncome = divs.reduce((s, d) => s + (Number(d.amount) || 0) * qtyForQuarter(h, quarterKey(d.date)), 0);
  const totalPerShare = divs.reduce((s, d) => s + (Number(d.amount) || 0), 0);

  el.historyTitle.innerHTML = `<span class="ticker-badge">${esc(h.ticker)}</span><span>${esc(h.name)}</span>`;
  el.historySub.textContent =
    (divs.length
      ? `${divs.length} dividend${divs.length === 1 ? '' : 's'} · ${fmtMoney(totalPerShare)} per share total`
      : 'No dividend history yet') +
    (h.price ? ` · Current price ${fmtMoney(h.price)}` : '');

  if (!divs.length) {
    el.historyBody.innerHTML = '<div class="history-empty">No dividend history loaded yet. Close this and press 🔄 to refresh.</div>';
  } else {
    const rows = divs.map(d => {
      const q = qtyForQuarter(h, quarterKey(d.date));
      const income = (Number(d.amount) || 0) * q;
      return `<tr>
        <td>${esc(fmtDate(d.date))}</td>
        <td>${esc(quarterKey(d.date).replace('-', ' '))}</td>
        <td class="num">${fmtMoney(d.amount)}</td>
        <td class="num">${q}</td>
        <td class="num income-cell">${fmtMoney(income)}</td>
      </tr>`;
    }).join('');
    el.historyBody.innerHTML = `<table class="history-table">
      <thead><tr><th>Date</th><th>Quarter</th><th class="num">₹ / share</th><th class="num">Qty</th><th class="num">Income</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="history-total"><span>Total dividends received</span><span class="income-cell">${fmtMoney(totalIncome)}</span></div>`;
  }
  el.historyModal.hidden = false;
}

function openHistory(ticker) {
  if (!getHolding(ticker)) return;
  state.historyTicker = ticker;
  renderHistory();
  el.historyClose.focus();
}

function closeHistory() {
  state.historyTicker = null;
  el.historyModal.hidden = true;
}

// ---------- analysis (ChromaDB) ----------
function populateAnalysisYears() {
  const years = availableYears(false);
  let value = state.analysisYear;
  if (value === null || value === undefined) {
    value = state.year === 'all' ? 'all' : state.year;
  }
  if (value !== 'all' && !years.includes(Number(value))) value = 'all';
  state.analysisYear = value;
  el.analysisYear.innerHTML = '<option value="all">All years</option>' +
    years.map(y => `<option value="${y}"${String(y) === String(value) ? ' selected' : ''}>${y}</option>`).join('');
  el.analysisYear.value = String(value);
}

function analysisBar(label, value, max, valueText, title) {
  const pct = max > 0 ? Math.max(2, Math.min(100, (Number(value) || 0) / max * 100)) : 0;
  return `<div class="bar-row" title="${esc(title || '')}">
    <span class="bar-label">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
    <span class="bar-value">${valueText}</span>
  </div>`;
}

function renderAnalysis(data) {
  const yearLabel = data.year === 'all' ? 'All years' : String(data.year);
  el.analysisSub.textContent =
    `ChromaDB · ${data.holdingsCount} holding${data.holdingsCount === 1 ? '' : 's'} · ` +
    `${data.totalPayouts} payout${data.totalPayouts === 1 ? '' : 's'} · ${yearLabel}`;

  if (!data.totalPayouts && !data.holdingsCount) {
    el.analysisBody.innerHTML = '<div class="analysis-empty">No data yet. Add stocks and refresh to populate the local database.</div>';
    return;
  }

  const maxYear = Math.max(1, ...data.years.map(y => y.income));
  const maxStock = Math.max(1, ...data.byStock.map(s => s.income));
  const maxQuarter = Math.max(1, ...data.byQuarter.map(q => q.income));

  const yearRows = data.years.length
    ? data.years.map(y => analysisBar(String(y.year), y.income, maxYear, fmtMoney(y.income), `${y.payouts} payout${y.payouts === 1 ? '' : 's'}`)).join('')
    : '<div class="analysis-empty">No payouts recorded.</div>';

  const stockRows = data.byStock.length
    ? data.byStock.map(s => analysisBar(
        s.ticker, s.income, maxStock,
        `${fmtMoney(s.income)} · ${s.sharePct}%`,
        `${s.name} · ${s.payouts} payout${s.payouts === 1 ? '' : 's'} · yield ${fmtPct(s.yieldPct)}`
      )).join('')
    : '<div class="analysis-empty">No payouts recorded.</div>';

  const quarterRows = data.byQuarter.map(q =>
    analysisBar(q.quarter, q.income, maxQuarter, fmtMoney(q.income), `${q.quarter} income · ${yearLabel}`)
  ).join('');

  const topRows = data.topPayouts.length
    ? data.topPayouts.map(p => `<div class="search-result">
        <div><span class="ticker-badge">${esc(p.ticker)}</span> <strong>${fmtMoney(p.amount)}</strong>/share
          <div class="meta">${esc(fmtDate(p.date))} · ${esc(p.quarter || '')} · Qty ${p.quantity}</div>
        </div>
        <div class="amount income-cell">${fmtMoney(p.income)}</div>
      </div>`).join('')
    : '<div class="analysis-empty">No payouts recorded.</div>';

  const cashRows = (data.cashflowByMonth || []).length ? `
    <div class="analysis-section">
      <h3>Monthly cashflow · ${esc(yearLabel)}</h3>
      ${data.cashflowByMonth.map(row => analysisBar(row.month, row.income,
        Math.max(...data.cashflowByMonth.map(r => r.income), 0.0001),
        fmtMoney(row.income), `${row.payouts} payout${row.payouts === 1 ? '' : 's'}`)).join('')}
    </div>` : '';

  const bridgeRows = data.incomeBridge && data.incomeBridge.delta !== null && data.incomeBridge.delta !== undefined ? `
    <div class="analysis-section">
      <h3>Income bridge · ${esc(data.incomeBridge.previousYear)} → ${esc(data.incomeBridge.currentYear)}</h3>
      <div class="meta">${fmtMoney(data.incomeBridge.previousIncome)} → ${fmtMoney(data.incomeBridge.currentIncome)}
        (${data.incomeBridge.delta >= 0 ? '+' : ''}${fmtMoney(data.incomeBridge.delta)}${data.incomeBridge.deltaPct !== null ? `, ${data.incomeBridge.deltaPct >= 0 ? '+' : ''}${data.incomeBridge.deltaPct}%` : ''})</div>
      <div class="bridge-pills" style="margin-top:8px">${(data.incomeBridge.components || []).map(c =>
        `<span class="bridge-pill">${esc(c.label)}: ${c.amount >= 0 ? '+' : ''}${fmtMoney(c.amount)}</span>`).join('')}</div>
    </div>` : '';

  const projectionRows = data.projections ? `
    <div class="analysis-section">
      <h3>Next year income scenarios · ${esc(yearLabel)}</h3>
      <div class="analysis-grid">
        <div class="analysis-card"><div class="label">Conservative</div><div class="value">${fmtMoney(data.projections.conservative.income)}</div></div>
        <div class="analysis-card"><div class="label">Base</div><div class="value purple">${fmtMoney(data.projections.base.income)}</div></div>
        <div class="analysis-card"><div class="label">Growth</div><div class="value green">${fmtMoney(data.projections.growth.income)}</div></div>
      </div>
      <div class="meta" style="margin-top:8px">${(data.projections.notes || []).map(n => esc(n)).join(' ')}</div>
    </div>` : '';

  el.analysisBody.innerHTML = `
    <div class="analysis-grid">
      <div class="analysis-card"><div class="label">Total income · ${esc(yearLabel)}</div><div class="value orange">${fmtMoney(data.totalIncome)}</div></div>
      <div class="analysis-card"><div class="label">Payouts</div><div class="value">${data.totalPayouts}</div></div>
      <div class="analysis-card"><div class="label">Avg. yield (TTM)</div><div class="value green">${fmtPct(data.avgYieldPct)}</div></div>
      <div class="analysis-card"><div class="label">Holdings</div><div class="value">${data.holdingsCount}</div></div>
    </div>
    ${cashRows}
    ${bridgeRows}
    ${projectionRows}
    <div class="analysis-section"><h3>Income by year</h3>${yearRows}</div>
    <div class="analysis-section"><h3>Income by stock · ${esc(yearLabel)}</h3>${stockRows}</div>
    <div class="analysis-section"><h3>Quarterly income · ${esc(yearLabel)}</h3>${quarterRows}</div>
    <div class="analysis-section"><h3>Largest payouts · ${esc(yearLabel)}</h3>${topRows}</div>
  `;
}

async function loadAnalysis() {
  el.analysisBody.innerHTML = '<div class="analysis-empty">Loading analysis from ChromaDB…</div>';
  try {
    const year = state.analysisYear === null ? 'all' : state.analysisYear;
    const res = await fetch('/api/analysis?year=' + analysisYearParam(year));
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    renderAnalysis(json);
  } catch (e) {
    el.analysisBody.innerHTML = '<div class="analysis-error">Could not load analysis: ' + esc(e.message || e) + '</div>';
  }
}

function openAnalysis() {
  if (state.insightOpen) closeInsight();
  if (state.coachOpen) closeCoach();
  state.analysisOpen = true;
  populateAnalysisYears();
  el.analysisModal.hidden = false;
  el.analysisClose.focus();
  loadAnalysis();
}

function closeAnalysis() {
  state.analysisOpen = false;
  el.analysisModal.hidden = true;
}

async function runSearch(query) {
  const q = String(query || el.analysisSearchInput.value || '').trim();
  if (!q) return;
  el.analysisSearchInput.value = q;
  el.analysisSearchResults.innerHTML = '<div class="analysis-empty">Searching ChromaDB…</div>';
  try {
    const res = await fetch('/api/search?q=' + encodeURIComponent(q) + '&limit=10');
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    renderSearchResults(json.results || [], q);
  } catch (e) {
    el.analysisSearchResults.innerHTML = '<div class="analysis-error">Search failed: ' + esc(e.message || e) + '</div>';
  }
}

function renderSearchResults(results, query) {
  if (!results.length) {
    el.analysisSearchResults.innerHTML = '<div class="analysis-empty">No matching payouts for “' + esc(query) + '”.</div>';
    return;
  }
  el.analysisSearchResults.innerHTML = results.map(r => {
    const score = (r.score === null || r.score === undefined) ? '' : ` · match ${Math.round(r.score * 100)}%`;
    return `<div class="search-result">
      <div><span class="ticker-badge">${esc(r.ticker)}</span> <strong>${fmtMoney(r.amount)}</strong>/share
        <div class="meta">${esc(fmtDate(r.date))} · ${esc(r.quarter || '')} · Qty ${r.quantity}${score}</div>
      </div>
      <div class="amount income-cell">${fmtMoney(r.income)}</div>
    </div>`;
  }).join('');
}

// ---------- portfolio insight ----------
// Everything below renders /api/insight. The payload is computed on
// demand and never persisted, so this is pure display code.

const INSIGHT_PALETTE = ['#ff9f0a', '#64d2ff', '#30d158', '#bf5af2', '#ffd60a', '#ff6961', '#5e5ce6', '#8e8e93'];
let insightRequest = 0;
let coachRequest = 0;
let coachBadgeToken = 0;

function insightBandColor(band) {
  return { strong: '#30d158', steady: '#64d2ff', uneven: '#ff9f0a', fragile: '#ff6961' }[band] || '#48484a';
}

function insightBandLabel(band) {
  return { strong: 'Strong', steady: 'Steady', uneven: 'Uneven', fragile: 'Fragile' }[band] || 'Not scored';
}

function insightNum(value, digits) {
  const n = Number(value);
  if (value === null || value === undefined || isNaN(n)) return '—';
  return n.toFixed(digits === undefined ? 1 : digits);
}

function insightScoreCell(score) {
  if (score === null || score === undefined) return '<span style="color:#8e8e93">—</span>';
  const color = score >= 75 ? '#30d158' : score >= 60 ? '#64d2ff' : score >= 40 ? '#ff9f0a' : '#ff6961';
  return `<span style="color:${color};font-weight:700">${insightNum(score, 1)}</span>`;
}

function insightRing(score, band) {
  const size = 120, r = 51, c = 2 * Math.PI * r, cx = 60, cy = 60;
  const pct = score === null || score === undefined ? 0 : Math.max(0, Math.min(100, Number(score)));
  const dash = (c * pct / 100).toFixed(1);
  return `<svg class="insight-ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img"
      aria-label="Overall dividend health score ${score === null ? 'not available' : Math.round(pct) + ' out of 100'}">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#3a3a3c" stroke-width="11"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${insightBandColor(band)}" stroke-width="11"
      stroke-linecap="round" stroke-dasharray="${dash} ${c.toFixed(1)}" transform="rotate(-90 ${cx} ${cy})"/>
    <text x="${cx}" y="${cy - 1}" text-anchor="middle" font-size="27" font-weight="800" fill="#fff">${score === null || score === undefined ? '—' : Math.round(pct)}</text>
    <text x="${cx}" y="${cy + 17}" text-anchor="middle" font-size="9" fill="#8e8e93" letter-spacing="1">/ 100</text>
  </svg>`;
}

function insightBar(label, value, max, valueText, title) {
  const pct = max > 0 ? Math.max(2, Math.min(100, (Number(value) || 0) / max * 100)) : 0;
  return `<div class="bar-row" title="${esc(title || '')}">
    <span class="bar-label">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
    <span class="bar-value">${valueText}</span>
  </div>`;
}

function insightList(items, emptyText) {
  if (!items || !items.length) {
    return `<div class="analysis-empty">${esc(emptyText)}</div>`;
  }
  return `<ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>`;
}

function insightYearChart(rows, year) {
  if (!rows.length) {
    return '<div class="insight-empty">No payouts recorded yet. Add dividend history to see this chart.</div>';
  }
  const w = Math.max(320, rows.length * 64), h = 178, pad = 28, base = h - 30;
  const max = Math.max(1, ...rows.map(row => Number(row.income) || 0));
  const slot = (w - pad * 2) / rows.length;
  const bw = Math.min(38, slot * 0.62);
  const bars = rows.map((row, index) => {
    const x = pad + (index + 0.5) * slot;
    const bh = Math.max(2, ((Number(row.income) || 0) / max) * (base - 22));
    const y = base - bh;
    const active = year !== 'all' && Number(row.year) === Number(year);
    return `<g>
      <rect x="${(x - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="4"
        fill="${active ? '#ff9f0a' : '#48484a'}"><title>${row.year}: ${fmtMoney(row.income)}</title></rect>
      <text x="${x.toFixed(1)}" y="${h - 10}" text-anchor="middle" font-size="10" fill="${active ? '#ff9f0a' : '#8e8e93'}">${row.year}</text>
    </g>`;
  }).join('');
  return `<svg class="insight-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img"
      aria-label="Dividend income by year, peak ${fmtMoney(max)}">
    <text x="${pad}" y="12" font-size="9" fill="#8e8e93">peak ${esc(fmtMoney(max))}</text>
    <line x1="${pad}" y1="${base}" x2="${w - pad}" y2="${base}" stroke="#3a3a3c" stroke-width="1"/>
    ${bars}
  </svg>`;
}

function insightDonut(rows, totalSources) {
  if (!rows.length) {
    return '<div class="insight-empty">No income to distribute for this period.</div>';
  }
  const size = 168, r = 62, cx = 84, cy = 84, c = 2 * Math.PI * r;
  let offset = 0;
  const slices = rows.map((row, index) => {
    const share = Math.max(0, Number(row.sharePct) || 0);
    const length = c * share / 100;
    const visible = Math.max(0, length - 2);
    const slice = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
      stroke="${INSIGHT_PALETTE[index % INSIGHT_PALETTE.length]}" stroke-width="21"
      stroke-dasharray="${visible.toFixed(2)} ${(c - visible).toFixed(2)}"
      stroke-dashoffset="${(-offset).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})">
      <title>${esc(row.label)}: ${share.toFixed(1)}%</title></circle>`;
    offset += length;
    return slice;
  }).join('');
  const legend = rows.map((row, index) => `<div class="row">
    <span class="dot" style="background:${INSIGHT_PALETTE[index % INSIGHT_PALETTE.length]}"></span>
    <span>${esc(row.label)}</span>
    <span class="val">${insightNum(row.sharePct, 1)}%</span>
  </div>`).join('');
  return `<div style="display:flex;gap:22px;align-items:center;flex-wrap:wrap">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Share of dividend income by holding">
      ${slices}
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="13" font-weight="700" fill="#fff">${totalSources || rows.length}</text>
      <text x="${cx}" y="${cy + 15}" text-anchor="middle" font-size="9" fill="#8e8e93">income sources</text>
    </svg>
    <div class="insight-legend" style="flex:1;min-width:180px">${legend}</div>
  </div>`;
}

function insightSparkline(dps) {
  const values = (dps || []).map(point => Number(point.amount) || 0);
  if (values.length < 2) return '<span style="color:#8e8e93">—</span>';
  const w = 68, h = 22, max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const step = w / (values.length - 1);
  const points = values.map((value, index) =>
    `${(index * step).toFixed(1)},${(h - 3 - ((value - min) / span) * (h - 6)).toFixed(1)}`
  ).join(' ');
  const rising = values[values.length - 1] >= values[0];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polyline points="${points}" fill="none" stroke="${rising ? '#30d158' : '#ff6961'}" stroke-width="1.8"
      stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

function insightKpi(label, value, cls, hint) {
  return `<div class="analysis-card" title="${esc(hint || '')}">
    <div class="label">${esc(label)}</div>
    <div class="value ${cls || ''}">${value}</div>
  </div>`;
}

function insightOverview(data) {
  const scores = data.scores, narrative = data.narrative, stats = data.stats;
  const confidence = Math.round((Number(scores.confidence) || 0) * 100);
  const components = scores.components.map(component => insightBar(
    component.label,
    component.score,
    100,
    component.score === null ? '—' : insightNum(component.score, 1),
    `${component.note} Weight ${Math.round(component.weight * 100)}%.`
  )).join('');

  const panels = stats.holdings === 0
    ? `<div class="insight-panel">
        <h4>Get started</h4>
        <div class="insight-summary">Add stocks and refresh their dividends to see strengths,
          risks and a watchlist here.</div>
      </div>`
    : stats.totalIncome <= 0
      ? `<div class="insight-panel bad">
          <h4>What is missing</h4>${insightList(narrative.risks, 'No income recorded for this period.')}
        </div>`
      : [
          ['good', 'Strengths', narrative.strengths, 'No clear strengths detected yet.'],
          ['bad', 'Risks', narrative.risks, 'No concentration or cut risks detected.'],
          ['warn', 'Watchlist', narrative.watchlist, 'Nothing on the watchlist.'],
        ].map(([tone, title, items, empty]) => `<div class="insight-panel ${tone}">
            <h4>${title}</h4>${insightList(items, empty)}
          </div>`).join('');

  const deferred = (scores.deferred || []).map(item => item.label).join(' · ');
  const noScoreNote = scores.overall === null && stats.holdings > 0
    ? `<div class="insight-deferred" style="margin-top:10px">Overall score withheld until this
        period has recorded income.</div>`
    : '';

  return `
    <div class="insight-hero">
      ${insightRing(scores.overall, scores.band)}
      <div style="flex:1;min-width:200px">
        <div class="insight-hero-title">Overall dividend health</div>
        <div class="meta">${esc(data.yearLabel)} · ${stats.holdings} holding${stats.holdings === 1 ? '' : 's'}
          · ${confidence}% data coverage</div>
        <span class="insight-band ${esc(scores.band)}">${esc(insightBandLabel(scores.band))}</span>
      </div>
    </div>
    <div class="insight-headline">${esc(narrative.headline)}</div>
    <div class="insight-summary">${esc(narrative.summary)}</div>
    ${noScoreNote}
    <div class="insight-subhead">Score components</div>
    ${components || '<div class="insight-empty">Not enough data to score this portfolio.</div>'}
    <div class="insight-columns">${panels}</div>
    <div class="insight-deferred">
      <strong>Not scored yet:</strong> ${esc(deferred || 'none')}.
      These signals are planned for a later phase and are excluded rather than scored as zero.
    </div>`;
}

function insightScenarioBand(stats, key, label, valueClass) {
  const band = stats.projections && stats.projections[key];
  if (!band || band.income === null) return '';
  const note = (stats.projections.notes || [])[{ conservative: 0, base: 1, growth: 2 }[key]] || '';
  return insightKpi(`${label} scenario`, fmtMoney(band.income), valueClass, note);
}

function insightIncome(data) {
  const stats = data.stats, isYear = data.year !== 'all';
  const projection = stats.projectedNextYearIncome;
  const change = stats.projectedIncreasePct;
  const scenarioCards = isYear && stats.projections
    ? insightScenarioBand(stats, 'conservative', 'Conservative', '') +
      insightScenarioBand(stats, 'base', 'Base', 'purple') +
      insightScenarioBand(stats, 'growth', 'Growth', 'green')
    : '';
  const cards = [
    insightKpi(`Total income · ${data.yearLabel}`, fmtMoney(stats.totalIncome), 'orange',
      'Dividend income recorded for the selected period.'),
    insightKpi('Payouts', String(stats.payouts), '', 'Dividend payments counted in this period.'),
    insightKpi('Avg. yield (TTM)', fmtPct(stats.avgYieldPct), 'green',
      'Average trailing yield across holdings with a known price.'),
    isYear && projection !== null
      ? insightKpi('Projected next year', fmtMoney(projection),
          change !== null && change >= 0 ? 'green' : 'orange',
          'Carries this year\'s peak quarterly quantity into next year at the same per-share dividend.')
      : insightKpi('Holdings with 3+ years', `${stats.holdingsWithHistory} / ${stats.holdings}`, '',
          'Holdings with enough dividend history for a reliable stability score.'),
  ].join('');

  const changeLine = isYear && change !== null
    ? `<div class="insight-summary" style="margin-top:8px">Projected change versus ${esc(data.yearLabel)}:
        <strong style="color:${change >= 0 ? '#30d158' : '#ff6961'}">${change >= 0 ? '+' : ''}${insightNum(change, 1)}%</strong></div>`
    : '';

  return `<div class="insight-kpis">${cards}</div>${changeLine}
    ${scenarioCards ? `<div class="insight-subhead">Next year scenarios</div><div class="insight-kpis">${scenarioCards}</div>` : ''}
    <div class="insight-subhead">Income by year</div>
    ${insightYearChart(data.charts.incomeByYear, data.year)}
    <div class="insight-subhead">Where the income comes from</div>
    <div class="insight-panel">${insightDonut(data.charts.concentration, stats.incomeSources)}</div>
    <div class="insight-deferred">
      Concentration uses actual income for ${esc(data.yearLabel)}, not portfolio value.
      ${stats.top1Pct === null ? '' : `Largest holding: ${insightNum(stats.top1Pct, 1)}% of income.`}
    </div>`;
}

function insightHoldings(data) {
  const rows = data.charts.holdings;
  if (!rows.length) {
    return '<div class="insight-empty">No holdings yet. Add stocks to see per-holding scores.</div>';
  }
  const body = rows.map(row => {
    const flags = [];
    if (row.cuts && row.cuts.length) {
      flags.push(`<span class="insight-flag cut" title="Dividend cut in ${row.cuts.map(cut => cut.year).join(', ')}">cut</span>`);
    }
    if (!row.hasPrice) {
      flags.push('<span class="insight-flag" title="No price on record, so yield is excluded">no price</span>');
    }
    if (row.yieldVsMedianPct !== null && row.yieldVsMedianPct !== undefined && row.yieldVsMedianPct > 10) {
      flags.push(`<span class="insight-flag cut" title="Yield is ${row.yieldVsMedianPct}% above this stock's own refresh median">yield rich</span>`);
    }
    if ((row.yearsOfHistory || 0) < 3) {
      flags.push('<span class="insight-flag" title="Fewer than three years of recorded dividend history">short history</span>');
    }
    return `<tr>
      <td><span class="ticker-badge">${esc(row.ticker)}</span>
        <div class="meta">${esc(row.name)}</div></td>
      <td class="num">${insightNum(row.sharePct, 1)}%</td>
      <td style="text-align:center">${insightSparkline(row.dps)}</td>
      <td class="num">${insightScoreCell(row.score)}</td>
      <td class="num">${insightNum(row.stability, 1)}</td>
      <td class="num">${row.cagrPct === null ? '—' : insightNum(row.cagrPct, 2) + '%'}</td>
      <td class="num">${row.streak}</td>
      <td>${flags.join(' ') || '<span style="color:#8e8e93">—</span>'}</td>
    </tr>`;
  }).join('');

  return `<div class="insight-table-wrap">
    <table class="insight-table">
      <thead><tr>
        <th>Holding</th>
        <th class="num">Income</th>
        <th style="text-align:center" title="Dividend per share, last five recorded years">Trend</th>
        <th class="num">Score</th>
        <th class="num">Stability</th>
        <th class="num">CAGR</th>
        <th class="num" title="Consecutive years with a payout">Streak</th>
        <th>Flags</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>
  </div>
  <div class="insight-deferred">
    Scores combine dividend stability, payment streak and growth. Holdings marked
    <em>short history</em> have fewer than three recorded years, so their stability is less reliable.
  </div>`;
}

function insightMethod(data) {
  const labels = {};
  data.scores.components.forEach(component => { labels[component.key] = component.label; });
  const weights = Object.entries(data.method.weights).map(([key, weight]) =>
    insightBar(labels[key] || key, weight * 100, 100, `${Math.round(weight * 100)}%`, key)
  ).join('');

  const thresholds = Object.entries(data.method.thresholds).map(([key, text]) =>
    `<dt>${esc(key.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase()))}</dt><dd>${esc(text)}</dd>`
  ).join('');

  const deferred = (data.scores.deferred || []).map(item =>
    `<div class="insight-panel" style="margin-top:8px">
      <h4>${esc(item.label)} · phase ${item.phase}</h4>
      <div class="insight-summary">${esc(item.reason)}</div>
    </div>`
  ).join('');

  return `<div class="insight-summary">Every score is computed from data already stored in this
      tracker. Nothing is sent anywhere, cached on disk, or written back.</div>
    <div class="insight-subhead">Weights</div>
    ${weights}
    <div class="insight-subhead">Formula notes</div>
    <dl class="insight-method">${thresholds}</dl>
    <div class="insight-subhead">Inputs used</div>
    <div class="insight-summary">${data.method.inputs.map(esc).join(' · ')}</div>
    <div class="insight-subhead">Deferred signals</div>
    ${deferred || '<div class="insight-empty">All planned signals are scored.</div>'}
    <div class="insight-subhead">Disclaimer</div>
    <div class="insight-summary">${esc(data.disclaimer)}</div>`;
}

function insightTabBody(data) {
  if (state.insightTab === 'income') return insightIncome(data);
  if (state.insightTab === 'holdings') return insightHoldings(data);
  if (state.insightTab === 'method') return insightMethod(data);
  return insightOverview(data);
}

function renderInsight(data) {
  state.insightData = data;
  const generated = new Date(data.generatedAt);
  const stamp = isNaN(generated.getTime())
    ? 'just now'
    : generated.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  el.insightSub.textContent =
    `Local data only · ${data.stats.holdings} holding${data.stats.holdings === 1 ? '' : 's'} · ` +
    `${data.yearLabel} · computed at ${stamp} · nothing stored`;
  populateInsightYears();
  setInsightTab(state.insightTab);
}

function populateInsightYears() {
  const years = availableYears(true);
  const selected = state.insightYear === null ? 'all' : String(state.insightYear);
  el.insightYear.innerHTML =
    years.map(year => `<option value="${year}">${year}</option>`).join('') +
    '<option value="all">All years</option>';
  el.insightYear.value = selected;
}

async function loadInsight() {
  const token = ++insightRequest;
  el.insightBody.innerHTML = '<div class="insight-empty">Computing portfolio insight from local data…</div>';
  try {
    const year = state.insightYear === null ? 'all' : state.insightYear;
    const res = await fetch('/api/insight?year=' + encodeURIComponent(year));
    const json = await res.json().catch(() => ({}));
    if (token !== insightRequest) return;
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    renderInsight(json);
  } catch (e) {
    if (token !== insightRequest) return;
    el.insightBody.innerHTML = '<div class="analysis-error">Could not compute insight: ' + esc(e.message || e) + '</div>';
  }
}

function renderCoachBadge(counts) {
  const urgent = counts && counts.p0 ? counts.p0 : 0;
  if (!el.coachBadge) return;
  if (urgent > 0) {
    el.coachBadge.hidden = false;
    el.coachBadge.textContent = String(urgent);
    el.coachBadge.title = `${urgent} urgent briefing item${urgent === 1 ? '' : 's'}`;
  } else {
    el.coachBadge.hidden = true;
    el.coachBadge.textContent = '';
  }
}

async function refreshCoachBadge() {
  const year = state.year === null || state.year === 'all' ? 'all' : state.year;
  const token = ++coachBadgeToken;
  try {
    const res = await fetch('/api/coach?year=' + encodeURIComponent(year));
    const json = await res.json().catch(() => ({}));
    if (token !== coachBadgeToken) return;
    if (!res.ok) return;
    renderCoachBadge(json.counts);
    if (state.coachOpen && state.coachData) {
      /* badge only */
    }
  } catch (e) {
    if (token !== coachBadgeToken) return;
  }
}

function coachPriorityLabel(priority) {
  return String(priority || '').toUpperCase();
}

function renderCoach(data) {
  state.coachData = data;
  const generated = new Date(data.generatedAt);
  const stamp = isNaN(generated.getTime())
    ? 'just now'
    : generated.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  el.coachSub.textContent =
    `${data.counts.total} task${data.counts.total === 1 ? '' : 's'} · ` +
    `${data.yearLabel} · computed at ${stamp} · nothing stored`;
  renderCoachBadge(data.counts);
  populateCoachYears();
  const tasks = data.tasks || [];
  const taskHtml = tasks.length
    ? `<ul class="coach-task-list">${tasks.map(task => `
      <li>
        <button type="button" class="coach-task" data-coach-action="${esc(task.action || '')}" data-coach-ticker="${esc(task.ticker || '')}" data-coach-quarter="${esc(task.quarter || '')}">
          <span class="coach-priority ${esc(task.priority || '')}">${esc(coachPriorityLabel(task.priority))}</span>
          ${esc(task.message)}
          ${task.ticker ? `<div class="meta">${esc(task.ticker)}${task.quarter ? ' · ' + esc(task.quarter) : ''}</div>` : ''}
        </button>
      </li>`).join('')}</ul>`
    : '<div class="insight-empty">No action items for this period.</div>';
  const events = (data.events || []).slice(0, 12);
  const eventsHtml = events.length
    ? `<div class="insight-subhead">Recent dividend context</div><ul class="coach-events">${events.map(ev => `<li>${esc(ev.message)}</li>`).join('')}</ul>`
    : '';
  el.coachBody.innerHTML = `
    <div class="coach-headline">${esc(data.briefing.headline)}</div>
    <div class="coach-summary">${esc(data.briefing.summary)}</div>
    <div class="insight-subhead">Tasks</div>
    ${taskHtml}
    ${eventsHtml}
    <div class="insight-deferred" style="margin-top:14px">${esc(data.disclaimer || '')}</div>`;
}

function populateCoachYears() {
  const years = availableYears(true);
  const selected = state.coachYear === null ? 'all' : String(state.coachYear);
  el.coachYear.innerHTML =
    years.map(year => `<option value="${year}">${year}</option>`).join('') +
    '<option value="all">All years</option>';
  el.coachYear.value = selected;
}

async function loadCoach() {
  const token = ++coachRequest;
  el.coachBody.innerHTML = '<div class="insight-empty">Building portfolio briefing from local data…</div>';
  try {
    const year = state.coachYear === null ? 'all' : state.coachYear;
    const res = await fetch('/api/coach?year=' + encodeURIComponent(year));
    const json = await res.json().catch(() => ({}));
    if (token !== coachRequest) return;
    if (!res.ok) throw new Error(json.error || ('HTTP ' + res.status));
    renderCoach(json);
  } catch (e) {
    if (token !== coachRequest) return;
    el.coachBody.innerHTML = '<div class="analysis-error">Could not load briefing: ' + esc(e.message || e) + '</div>';
  }
}

function focusCoachQuarter(ticker, quarterKey) {
  if (!ticker || !quarterKey) return;
  const year = parseInt(String(quarterKey).split('-')[0], 10);
  if (!Number.isNaN(year)) state.year = year;
  render();
  closeCoach();
  requestAnimationFrame(() => {
    const input = document.querySelector(
      `input.qty-input[data-ticker="${CSS.escape(ticker)}"][data-quarter="${CSS.escape(quarterKey)}"]`
    );
    if (input) {
      input.scrollIntoView({ block: 'center', behavior: 'smooth' });
      input.focus();
      input.select();
    }
  });
}

function runCoachAction(button) {
  const action = button.dataset.coachAction;
  const ticker = button.dataset.coachTicker;
  const quarter = button.dataset.coachQuarter;
  if (action === 'focus-quarter') focusCoachQuarter(ticker, quarter);
  else if (action === 'refresh' && ticker) {
    closeCoach();
    refreshTicker(ticker);
  } else if (action === 'open-insight') {
    closeCoach();
    openInsight();
  } else if (action === 'open-analysis') {
    closeCoach();
    openAnalysis();
  }
}

function openCoach() {
  if (state.analysisOpen) closeAnalysis();
  if (state.insightOpen) closeInsight();
  state.coachOpen = true;
  if (state.coachYear === null) {
    const years = availableYears(true);
    if (state.year !== 'all' && state.year !== null && years.includes(state.year)) {
      state.coachYear = state.year;
    } else {
      state.coachYear = years.length ? years[0] : 'all';
    }
  }
  el.coachModal.hidden = false;
  el.coachClose.focus();
  loadCoach();
}

function closeCoach() {
  state.coachOpen = false;
  el.coachModal.hidden = true;
}

function setInsightTab(tab) {
  state.insightTab = tab;
  el.insightTabs.querySelectorAll('.insight-tab').forEach(button => {
    const active = button.dataset.tab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  if (state.insightData) el.insightBody.innerHTML = insightTabBody(state.insightData);
}

function openInsight() {
  if (state.analysisOpen) closeAnalysis();
  if (state.coachOpen) closeCoach();
  state.insightOpen = true;
  if (state.insightYear === null) {
    const years = availableYears(true);
    if (state.year !== 'all' && state.year !== null && years.includes(state.year)) {
      state.insightYear = state.year;
    } else {
      state.insightYear = years.length ? years[0] : new Date().getFullYear();
    }
  }
  populateInsightYears();
  setInsightTab(state.insightTab || 'overview');
  el.insightModal.hidden = false;
  el.insightClose.focus();
  loadInsight();
}

function closeInsight() {
  state.insightOpen = false;
  el.insightModal.hidden = true;
}

function render() {
  renderYearFilter();
  renderSummary();
  renderTable();
  renderHistory();
  loadSummaryProjections();
  refreshCoachBadge();
}
// ---------- events ----------
el.yearFilter.addEventListener('change', () => {
  const v = el.yearFilter.value;
  state.year = v === 'all' ? 'all' : (v.startsWith('fy:') ? v : Number(v));
  render();
});

function toggleSort(key) {
  // Re-rendering replaces the header cells, so remember whether the user
  // was sorting from the keyboard and restore focus to the same header.
  const restoreFocus = !!(document.activeElement && document.activeElement.matches &&
    document.activeElement.matches('th[data-sort]'));
  if (state.sortKey === key) {
    state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    state.sortKey = key;
    state.sortDir = defaultSortDir(key);
  }
  renderTable();
  if (restoreFocus) {
    const th = el.tableHead.querySelector(`th[data-sort="${key}"]`);
    if (th) th.focus();
  }
}

el.tableHead.addEventListener('click', e => {
  const th = e.target.closest('th[data-sort]');
  if (!th) return;
  toggleSort(th.dataset.sort);
});

el.tableHead.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const th = e.target.closest('th[data-sort]');
  if (!th) return;
  e.preventDefault();
  toggleSort(th.dataset.sort);
});

function clearSelectedYear() {
  const year = state.year;
  if (year === 'all') {
    showFormMessage('Select a specific year before clearing.', 'error');
    return;
  }
  const affected = [];
  for (const h of state.holdings) {
    const keys = Object.keys(h.quarters || {}).filter(k => k.startsWith(year + '-'));
    if (keys.length) affected.push({ holding: h, keys });
  }
  if (!affected.length) {
    showFormMessage(`No saved quantities for ${year}.`, 'info');
    return;
  }
  const total = affected.reduce((sum, item) => sum + item.keys.length, 0);
  if (!window.confirm(`Set all ${total} saved quarter quantities for ${year} to 0? This cannot be undone.`)) return;
  for (const { holding, keys } of affected) {
    for (const key of keys) {
      holding.quarters[key] = 0;
      touch(holding, key);
    }
  }
  persist();
  dbImportAll(affected.map(item => item.holding));
  el.formMsg.textContent = `Cleared ${total} quarter value(s) for ${year}. Enter the correct quantities.`;
  el.formMsg.className = 'ok';
}

el.clearYearBtn.addEventListener('click', clearSelectedYear);

el.addForm.addEventListener('submit', e => {
  e.preventDefault();
  addTickers(el.addInput.value, el.addShares.value);
  el.addInput.value = '';
});

el.emptyState.addEventListener('click', e => {
  const btn = e.target.closest('button[data-example]');
  if (!btn) return;
  addTickers(btn.dataset.example, el.addShares.value);
});

el.refreshAllBtn.addEventListener('click', refreshAll);

el.errorDismiss.addEventListener('click', hideFetchError);
el.errorRetry.addEventListener('click', retryFailedFetch);
el.errorCopy.addEventListener('click', async () => {
  const text = errorDebugText();
  let ok = false;
  try { ok = await copyText(text); } catch (e) { ok = false; }
  if (!ok) {
    window.prompt('Copy the debug info below (Ctrl/Cmd+C):', text);
    return;
  }
  el.errorCopy.textContent = 'Copied ✓';
  setTimeout(() => { el.errorCopy.textContent = 'Copy debug info'; }, 1800);
});

el.historyClose.addEventListener('click', closeHistory);
el.historyModal.addEventListener('click', e => { if (e.target === el.historyModal) closeHistory(); });

el.importBtn.addEventListener('click', openImport);
el.importClose.addEventListener('click', closeImport);
el.importCancel.addEventListener('click', closeImport);
el.importModal.addEventListener('click', e => { if (e.target === el.importModal) closeImport(); });
el.importRun.addEventListener('click', runImport);
el.importFile.addEventListener('change', async () => {
  const file = el.importFile.files && el.importFile.files[0];
  if (!file) return;
  try {
    el.importText.value = await file.text();
    el.importMsg.textContent = '';
  } catch (e) {
    el.importMsg.textContent = 'Could not read that file: ' + (e.message || e);
  }
});
el.importText.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runImport(); }
});

el.analysisBtn.addEventListener('click', openAnalysis);
el.analysisClose.addEventListener('click', closeAnalysis);
el.analysisModal.addEventListener('click', e => { if (e.target === el.analysisModal) closeAnalysis(); });
el.analysisYear.addEventListener('change', () => {
  const v = el.analysisYear.value;
  state.analysisYear = v === 'all' ? 'all' : Number(v);
  loadAnalysis();
});
el.analysisRefreshBtn.addEventListener('click', loadAnalysis);
el.analysisSearchBtn.addEventListener('click', () => runSearch());
el.analysisSearchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); runSearch(); }
});
el.analysisChips.addEventListener('click', e => {
  const chip = e.target.closest('.chip');
  if (chip) runSearch(chip.dataset.query);
});

el.coachBtn.addEventListener('click', openCoach);
el.coachClose.addEventListener('click', closeCoach);
el.coachModal.addEventListener('click', e => { if (e.target === el.coachModal) closeCoach(); });
el.coachYear.addEventListener('change', () => {
  const value = el.coachYear.value;
  state.coachYear = value === 'all' ? 'all' : Number(value);
  loadCoach();
});
el.coachReload.addEventListener('click', loadCoach);
el.coachBody.addEventListener('click', e => {
  const btn = e.target.closest('[data-coach-action]');
  if (!btn) return;
  runCoachAction(btn);
});

el.insightBtn.addEventListener('click', openInsight);
el.insightClose.addEventListener('click', closeInsight);
el.insightModal.addEventListener('click', e => { if (e.target === el.insightModal) closeInsight(); });
el.insightTabs.addEventListener('click', e => {
  const tab = e.target.closest('.insight-tab');
  if (tab) setInsightTab(tab.dataset.tab);
});
el.insightYear.addEventListener('change', () => {
  const value = el.insightYear.value;
  state.insightYear = value === 'all' ? 'all' : Number(value);
  loadInsight();
});
el.insightReload.addEventListener('click', loadInsight);

document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (state.historyTicker) closeHistory();
  else if (!el.importModal.hidden) closeImport();
  else if (state.coachOpen) closeCoach();
  else if (state.insightOpen) closeInsight();
  else if (state.analysisOpen) closeAnalysis();
});

el.holdingsBody.addEventListener('click', e => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const ticker = btn.dataset.ticker;
  const action = btn.dataset.action;
  if (action === 'history') openHistory(ticker);
  if (action === 'refresh') {
    refreshTicker(ticker).then(result => {
      if (!result) return;
      if (result.ok) {
        clearFetchError(ticker);
        showFormMessage('Refreshed ' + ticker + '.', 'ok');
      } else {
        reportFetchErrors([result]);
      }
    });
  }
  if (action === 'delete') {
    if (state.historyTicker === ticker) closeHistory();
    clearFetchError(ticker);
    // Tombstone first: this blocks the in-flight refresh/poll from
    // re-saving the row after the DELETE request.
    deletedTickers.set(ticker, Date.now());
    if (syncTimers[ticker]) { clearTimeout(syncTimers[ticker]); delete syncTimers[ticker]; }
    delete pendingDefaults[ticker];
    state.holdings = state.holdings.filter(h => h.ticker !== ticker);
    persist();
    dbDeleteHolding(ticker);
  }
});

// Save on every keystroke (without re-rendering, so focus is kept),
// then re-render on change to refresh the income totals.
el.holdingsBody.addEventListener('input', e => {
  const input = e.target.closest('.qty-input');
  if (!input) return;
  const h = getHolding(input.dataset.ticker);
  if (!h) return;
  h.quarters[input.dataset.quarter] = Math.max(0, parseInt(input.value, 10) || 0);
  touch(h, input.dataset.quarter);
  saveHoldings();
  scheduleSync(h);
});

el.holdingsBody.addEventListener('change', e => {
  const input = e.target.closest('.qty-input');
  if (!input) return;
  const h = getHolding(input.dataset.ticker);
  if (!h) return;
  h.quarters[input.dataset.quarter] = Math.max(0, parseInt(input.value, 10) || 0);
  touch(h, input.dataset.quarter);
  if (syncTimers[h.ticker]) { clearTimeout(syncTimers[h.ticker]); delete syncTimers[h.ticker]; }
  persist();
  dbSaveHolding(h, true);
});

el.holdingsBody.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('qty-input')) {
    e.preventDefault();
    e.target.blur();
  }
});

// ---------- cross-window sync ----------
document.addEventListener('focusout', () => {
  setTimeout(() => {
    if (!isEditingQuantity() && pendingRemote) {
      const pending = pendingRemote;
      pendingRemote = null;
      applyRemote(pending.remote, pending.revision);
    }
  }, 0);
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) schedulePoll(0);
});
window.addEventListener('focus', () => schedulePoll(0));
window.addEventListener('online', () => schedulePoll(0));
window.addEventListener('storage', e => {
  if (e.key === LS_HOLDINGS) schedulePoll(50);
});

// ---------- init ----------
async function init() {
  const local = loadHoldings().map(normalizeHolding);
  const initialRemote = (INITIAL.holdings || []).map(normalizeHolding);
  const knownRemote = loadSyncedTickers();
  state.holdings = mergeHoldings(local, initialRemote, knownRemote);
  syncedTickers = new Set(initialRemote.map(h => h.ticker));
  saveSyncedTickers();
  saveHoldings();
  render();

  lastRevision = Number(INITIAL.revision) || 0;
  const status = INITIAL.storage || {};
  if (status.backend === 'chromadb') {
    const count = status.holdings !== undefined ? status.holdings : state.holdings.length;
    setDbStatus(
      'saved',
      'DB: saved',
      'Local ChromaDB · revision ' + lastRevision + ' · ' + count + ' holdings'
    );
  } else if (status.error) {
    setDbStatus(
      'error',
      'DB: offline — local cache',
      'ChromaDB unavailable: ' + status.error + '. Data is still saved in this browser.'
    );
  } else {
    setDbStatus('connecting', 'DB: connecting…', 'Connecting to local ChromaDB…');
  }

  // Push local-only or newer records, then reconcile once in case another
  // window wrote while this page was loading.
  const push = localChangesToPush(local, initialRemote, knownRemote);
  if (push.length) {
    await Promise.all(push.map(h => dbSaveHolding(h, true)));
  }
  syncReady = true;
  await pollNow();
  if (!state.holdings.length) {
    setDbStatus('saved', 'DB: saved', 'Connected to local ChromaDB (0 holdings)');
  }

  startPolling();
  refreshAll();
}
init();
