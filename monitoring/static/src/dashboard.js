// ── State ───────────────────────────────────────────────────────────────────
const EXCHANGES = ['binance', 'kraken', 'htx'];

let currentFilter   = 'all';
let pollTimers       = {};   // { jobId: intervalId }
let elapsedTimer      = null;
let tradingMode       = 'paper';
let activeJobs        = {};  // { jobId: context }
let jobHistory        = [];  // [{context, status, time}] most-recent-first, capped
let alertState        = {};
let lastJournalRows   = [];
let lastTradingData   = null;
let lastLeaderboardRows = [];

const JOB_TAB_MAP = {
  data: 'data', features: 'data',
  backtest: 'simulate', validate: 'simulate', engine: 'simulate',
  research: 'research',
  'train-classifier': 'ops',
  'paper-cycle': 'trading', 'live-cycle': 'trading', 'paper-backfill': 'trading',
};

// ── Boot ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadStrategies();
  loadSymbols();
  loadDataStatus();
  loadLeaderboard('all');
  fetchResearchDecisions();
  loadTradingConfig();
  loadSimLeaderSummary();
  loadTradingStatus();
  initNavPin();
  clockTick();
  setInterval(clockTick, 1000);
  setInterval(refreshGlobalRiskStrip, 20000);
  document.addEventListener('keydown', (e) => {
    if (e.altKey && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); openKillModal(); }
    if (e.key === 'Escape') closeKillModal();
  });
  document.getElementById('kill-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeKillModal();
  });
});

function clockTick() {
  const el = document.getElementById('clock');
  if (el) el.textContent = new Date().toUTCString().slice(17, 25) + ' UTC';
}

// ── Nav pin ───────────────────────────────────────────────────────────────────
function initNavPin() {
  if (localStorage.getItem('navPinned') === '1') applyNavPinned(true);
}
function applyNavPinned(pinned) {
  document.getElementById('nav').classList.toggle('pinned', pinned);
  const btn = document.getElementById('pin-btn');
  if (btn) btn.textContent = pinned ? '⊟' : '⊞';
}
function toggleNavPin() {
  const pinned = !document.getElementById('nav').classList.contains('pinned');
  localStorage.setItem('navPinned', pinned ? '1' : '0');
  applyNavPinned(pinned);
}

// ── Tab switching (desktop nav + mobile nav share the same panels) ─────────
function activateTab(name) {
  document.querySelectorAll('.tab-content').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('tab-' + name);
  if (panel) panel.classList.add('active');

  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  const navBtn = document.querySelector('.nav-item[data-tab="' + name + '"]');
  if (navBtn) navBtn.classList.add('active');

  document.querySelectorAll('.mob-nav-item').forEach(b => b.classList.remove('active'));
  const mobBtn = document.getElementById('mob-' + name);
  if (mobBtn) mobBtn.classList.add('active');

  const main = document.getElementById('main');
  if (main) main.scrollTop = 0;

  if (name === 'results') loadLeaderboard(currentFilter);
  if (name === 'trading') loadTradingStatus();
  if (name === 'ops') loadModelConfidenceOptions();
  if (name === 'simulate') loadSimLeaderSummary();
}
function switchTab(name) { activateTab(name); }
function mobTab(name) { activateTab(name); }

// ── Data status ──────────────────────────────────────────────────────────────
async function loadDataStatus() {
  try {
    const r = await fetch('/api/data-status');
    const rows = await r.json();
    const tbody = document.getElementById('data-status-body');
    if (!Array.isArray(rows) || !rows.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No data. Run collection first.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(row => `
      <tr>
        <td>${esc(row.symbol)}</td>
        <td style="color:var(--text2)">${esc(row.exchange)}</td>
        <td class="num" style="color:var(--text3)">${esc(row.earliest)}</td>
        <td class="num">${esc(row.latest)}</td>
        <td class="num" style="color:var(--text3)">${row.bars.toLocaleString()}</td>
        <td><span class="tag tag-${row.status === 'fresh' ? 'green' : row.status === 'stale' ? 'cyan' : 'red'}">${row.status}${row.stale_days > 1 ? ' (+' + row.stale_days + 'd)' : ''}</span></td>
      </tr>
    `).join('');
    if (!window.__staleAlerted && rows.some(r => r.status === 'old')) {
      window.__staleAlerted = true;
      showToast('One or more feeds are critically stale — run data collection', 'error');
    }
  } catch (e) {
    document.getElementById('data-status-body').innerHTML =
      '<tr class="empty-row"><td colspan="6">Error loading status.</td></tr>';
  }
}

// ── Collect ──────────────────────────────────────────────────────────────────
async function runCollect(funding) {
  const btnId = funding ? 'btn-collect-funding' : 'btn-collect';
  const label = funding ? 'Refresh Funding' : 'Refresh OHLCV';
  const btn = document.getElementById(btnId);
  btn.disabled = true; btn.textContent = 'Running…';
  const exchange = document.getElementById('collect-exchange') ? document.getElementById('collect-exchange').value : 'binance';

  try {
    const r = await fetch('/api/collect', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({funding, exchange})
    });
    if (r.status === 409) {
      showToast('Collection already running', 'info');
      btn.disabled = false; btn.textContent = label; return;
    }
    const {job_id} = await r.json();
    document.getElementById('data-job-wrap').style.display = 'block';
    pollJob(job_id, 'data', () => {
      btn.disabled = false; btn.textContent = label;
      loadDataStatus();
      showToast('Data refresh complete', 'success');
    });
  } catch(e) {
    btn.disabled = false; btn.textContent = label;
    showToast('Error: ' + e, 'error');
  }
}

// ── Generic single-shot job runner (features / backtest / validate / train-classifier) ──
async function runSimpleJob(url, jobType, btnId, busyLabel, idleLabel) {
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = true; btn.textContent = busyLabel; }
  try {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'});
    if (r.status === 409) {
      showToast(jobType + ' already running', 'info');
      if (btn) { btn.disabled = false; btn.textContent = idleLabel; }
      return;
    }
    const {job_id} = await r.json();
    const wrap = document.getElementById(jobType + '-job-wrap');
    if (wrap) wrap.style.display = 'block';
    pollJob(job_id, jobType, (job) => {
      if (btn) { btn.disabled = false; btn.textContent = idleLabel; }
      const ok = job.status === 'done';
      showToast(idleLabel + (ok ? ' complete' : ' failed — check log'), ok ? 'success' : 'error');
      if (jobType === 'features') loadDataStatus();
      if (jobType === 'backtest' || jobType === 'validate') loadSimLeaderSummary();
    });
  } catch(e) {
    if (btn) { btn.disabled = false; btn.textContent = idleLabel; }
    showToast('Error: ' + e, 'error');
  }
}

// ── Engine ────────────────────────────────────────────────────────────────────
async function loadStrategies() {
  try {
    const r = await fetch('/api/strategies');
    const strats = await r.json();
    if (!Array.isArray(strats)) return;
    const opts = '<option value="">All strategies</option>' +
      strats.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('');
    const engineSel = document.getElementById('engine-strategy');
    const researchSel = document.getElementById('research-strategy');
    if (engineSel) engineSel.innerHTML = opts;
    if (researchSel) researchSel.innerHTML = opts;
    const tagsEl = document.getElementById('strat-tags');
    if (tagsEl) tagsEl.innerHTML = strats.map(s => `<span class="tag tag-cyan">${esc(s.name)}</span>`).join('');
  } catch(e) {}
}

async function loadSymbols() {
  try {
    const r = await fetch('/api/symbols');
    const syms = await r.json();
    if (!Array.isArray(syms)) return;
    const opts = '<option value="">All symbols</option>' +
      syms.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    const engineSym = document.getElementById('engine-symbol');
    if (engineSym) engineSym.innerHTML = opts;
    const driftSym = document.getElementById('drift-symbol');
    if (driftSym) driftSym.innerHTML = syms.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    const confSym = document.getElementById('confidence-symbol');
    if (confSym) confSym.innerHTML = syms.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
  } catch(e) {}
}

async function runEngine() {
  const btn = document.getElementById('btn-engine');
  btn.disabled = true; btn.textContent = 'Simulating…';

  const strategy = document.getElementById('engine-strategy').value;
  const symbol   = document.getElementById('engine-symbol').value;
  const body = {};
  if (strategy) body.strategy = strategy;
  if (symbol)   body.symbol   = symbol;

  try {
    const r = await fetch('/api/engine', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    if (r.status === 409) {
      showToast('Engine already running', 'info');
      btn.disabled = false; btn.textContent = 'Run Simulation'; return;
    }
    const {job_id} = await r.json();
    document.getElementById('engine-job-wrap').style.display = 'block';
    startElapsed('engine-job-elapsed');
    pollJob(job_id, 'engine', (job) => {
      btn.disabled = false; btn.textContent = 'Run Simulation';
      stopElapsed();
      if (job.status === 'done') {
        loadLeaderboard(currentFilter);
        loadSimLeaderSummary();
        showToast('Simulation complete', 'success');
      } else {
        showToast('Simulation failed — check log', 'error');
      }
    });
  } catch(e) {
    btn.disabled = false; btn.textContent = 'Run Simulation';
    showToast('Error: ' + e, 'error');
  }
}

// ── Simulate tab: current leaderboard leader snapshot ──────────────────────
async function loadSimLeaderSummary() {
  const el = document.getElementById('sim-leader-summary');
  if (!el) return;
  try {
    const r = await fetch('/api/leaderboard?tier=all');
    const rows = await r.json();
    if (!Array.isArray(rows) || !rows.length) {
      el.innerHTML = '<div class="section-sub" style="margin:0">No results yet — run the engine first.</div>';
      return;
    }
    const top = rows.reduce((best, r) => (r.avg_sharpe > best.avg_sharpe ? r : best), rows[0]);
    el.innerHTML = `
      <div class="section-sub" style="margin-bottom:10px">${esc(top.strategy)} / ${esc(top.symbol)}</div>
      <div class="grid2" style="gap:7px;margin-bottom:10px">
        <div class="metric"><div class="metric-label">Sharpe</div><div class="metric-val num ${top.avg_sharpe > 0 ? 'pos' : 'neg'}">${top.avg_sharpe.toFixed(2)}</div></div>
        <div class="metric"><div class="metric-label">Win Rate</div><div class="metric-val num">${top.avg_win_rate.toFixed(0)}%</div></div>
        <div class="metric"><div class="metric-label">Avg DD</div><div class="metric-val num">${top.avg_dd.toFixed(1)}%</div></div>
        <div class="metric"><div class="metric-label">Pass %</div><div class="metric-val num" style="color:var(--accent)">${top.pass_pct}%</div></div>
      </div>
      <div style="padding:8px;border-radius:4px;font-size:10px;${top.qualifies
        ? 'background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.2);color:var(--success)'
        : 'background:rgba(239,68,68,.07);border:1px solid rgba(239,68,68,.2);color:var(--danger)'}">
        ${top.qualifies ? '✓ Promoted — all gates cleared' : '✗ Not promoted — ' + esc(top.reason || 'gates not cleared')}
      </div>`;
  } catch(e) {
    el.innerHTML = '<div class="section-sub" style="margin:0">Error loading leaderboard.</div>';
  }
}

// ── Research ─────────────────────────────────────────────────────────────────
async function runResearch() {
  const btn = document.getElementById('btn-research');
  btn.disabled = true; btn.textContent = 'Running…';

  const body = {
    tier:     document.getElementById('research-tier').value,
    top_n:    parseInt(document.getElementById('research-topn').value),
    strategy: document.getElementById('research-strategy').value,
  };

  try {
    const r = await fetch('/api/research', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
    });
    if (r.status === 409) {
      showToast('Research already running', 'info');
      btn.disabled = false; btn.textContent = 'Run Research Pipeline'; return;
    }
    const {job_id} = await r.json();
    document.getElementById('research-job-wrap').style.display = 'block';
    pollJob(job_id, 'research', async (job) => {
      btn.disabled = false; btn.textContent = 'Run Research Pipeline';
      await fetchResearchDecisions();
      const status = job.status === 'done' ? 'success' : 'error';
      const msg = job.status === 'done' ? 'Research pipeline complete' : 'Research pipeline failed — check log';
      showToast(msg, status);
    });
  } catch(e) {
    btn.disabled = false; btn.textContent = 'Run Research Pipeline';
    showToast('Error: ' + e, 'error');
  }
}

async function fetchResearchDecisions() {
  try {
    const r = await fetch('/api/research-decisions?last=50');
    const decisions = await r.json();
    if (!Array.isArray(decisions)) return;
    renderResearchDecisions(decisions);
  } catch(e) { /* silently skip — decisions log may not exist yet */ }
}

function renderResearchDecisions(decisions) {
  const container = document.getElementById('research-decisions');
  if (!decisions.length) { container.innerHTML = ''; return; }

  const accepted = decisions.filter(d => d.accepted).length;
  const rejected = decisions.length - accepted;
  container.innerHTML =
    `<div style="font-size:13px;font-weight:600;margin-bottom:10px">` +
    `${accepted} accepted / ${rejected} rejected (most recent ${decisions.length})</div>` +
    decisions.map(d => `
      <div class="decision-card ${d.accepted ? 'accepted' : 'rejected'}">
        <div class="decision-top">
          <span class="decision-name">${esc(d.strategy_name)}${d.symbol ? ' / ' + esc(d.symbol) : ''}</span>
          <span class="tag ${d.accepted ? 'tag-green' : 'tag-red'}">${d.accepted ? 'ACCEPTED' : 'REJECTED'}</span>
        </div>
        <div class="decision-reason">${esc(d.reason)}</div>
        <div class="decision-meta">
          <span class="num">Sharpe: ${(d.avg_sharpe||0).toFixed(2)}</span>
          <span class="num">Pass: ${((d.pass_ratio||0)*100).toFixed(1)}%</span>
          <span class="num">Windows: ${d.n_windows||0}</span>
          <span style="color:var(--text3);margin-left:auto">${(d.timestamp||'').slice(0,10)}</span>
        </div>
      </div>
    `).join('');
}

// ── Leaderboard (collapsible rows) ───────────────────────────────────────────
async function loadLeaderboard(filter) {
  currentFilter = filter;
  document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('pipe-btn-active'));
  const activeBtn = document.querySelector('[data-filter="' + filter + '"]');
  if (activeBtn) activeBtn.classList.add('pipe-btn-active');

  const el = document.getElementById('leaderboard');
  el.innerHTML = '<div class="section-sub">Loading…</div>';

  try {
    const r = await fetch('/api/leaderboard?tier=' + filter);
    const rows = await r.json();

    if (rows.error) { el.innerHTML = `<div class="section-sub">Error: ${esc(rows.error)}</div>`; return; }
    if (!rows.length) { el.innerHTML = '<div class="section-sub">No results. Run the simulation first (Simulate tab).</div>'; return; }

    lastLeaderboardRows = rows;
    el.innerHTML = rows.map((row, i) => `
      <div class="lb-row" id="lb-row-${i}" onclick="toggleLbRow(${i})">
        <div class="lb-header">
          <span class="lb-rank">#${i + 1}</span>
          <span class="lb-name">${esc(row.strategy)} / ${esc(row.symbol)}</span>
          <span class="lb-reason">${esc(row.reason || '')}</span>
          <span class="lb-sharpe ${row.avg_sharpe > 0 ? 'pos' : 'neg'}">${row.avg_sharpe.toFixed(2)}</span>
          <span class="lb-wr">${row.avg_win_rate.toFixed(0)}%</span>
          <span class="lb-score">${row.pass_pct}%</span>
        </div>
        <div class="lb-detail" id="lb-detail-${i}"></div>
      </div>`
    ).join('');
  } catch(e) {
    el.innerHTML = '<div class="section-sub">Error loading leaderboard.</div>';
  }
}

function toggleLbRow(i) {
  const row = document.getElementById('lb-row-' + i);
  const wasOpen = row.classList.contains('open');
  document.querySelectorAll('.lb-row.open').forEach(r => r.classList.remove('open'));
  if (wasOpen) return;
  row.classList.add('open');
  const data = lastLeaderboardRows[i];
  if (data) loadDetail(data.strategy, data.symbol, i);
}

// ── Detail drill-down (rendered inside the row itself) ──────────────────────
async function loadDetail(strategy, symbol, i) {
  const el = document.getElementById('lb-detail-' + i);
  el.innerHTML = '<div class="section-sub">Loading…</div>';
  try {
    const r = await fetch('/api/engine-detail?strategy=' + encodeURIComponent(strategy) +
                          '&symbol=' + encodeURIComponent(symbol));
    const rows = await r.json();
    if (!rows.length) { el.innerHTML = '<div class="section-sub">No window data found.</div>'; return; }

    el.innerHTML = `<table class="wf-table">
      <thead><tr><th>Start</th><th>End</th><th>Yrs</th><th>Type</th><th>Return</th><th>Max DD</th><th>Sharpe</th><th>Win Rate</th><th>Trades</th><th>Pass</th><th>Params</th></tr></thead>
      <tbody>${rows.map(w => {
        const params = Object.entries(w.params||{}).map(([k,v])=>`${k}=${v}`).join(', ') || '—';
        return `<tr>
          <td>${esc(w.start)}</td><td>${esc(w.end)}</td><td>${w.years}y</td>
          <td style="color:var(--text3)">${esc(w.type)}</td>
          <td class="${w.ret >= 9 ? 'wf-pass' : ''}">${w.ret.toFixed(2)}%</td>
          <td class="${w.dd <= 3 ? 'wf-pass' : w.dd <= 10 ? '' : 'wf-fail'}">${w.dd.toFixed(2)}%</td>
          <td class="${w.sharpe > 0 ? 'wf-pass' : 'wf-fail'}">${w.sharpe.toFixed(3)}</td>
          <td>${w.wr.toFixed(1)}%</td>
          <td style="color:var(--text3)">${w.trades}</td>
          <td class="${w.pass ? 'wf-pass' : 'wf-fail'}">${w.pass ? '✓ PASS' : '✗ FAIL'}</td>
          <td style="color:var(--text3);font-size:10px">${esc(params)}</td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
  } catch(e) {
    el.innerHTML = '<div class="section-sub">Error loading detail.</div>';
  }
}

// ── Ops ───────────────────────────────────────────────────────────────────────
async function postAction(url, body) {
  const el = document.getElementById('ops-result');
  el.style.display = 'block';
  el.textContent = 'Running ' + url + ' …';
  try {
    const r = await fetch(url, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body||{})
    });
    const data = await r.json();
    el.textContent = JSON.stringify(data, null, 2);
    return data;
  } catch(err) {
    el.textContent = 'Error: ' + err;
  }
}

function retrainNow() {
  postAction('/api/retrain', {}).then(() => showToast('Retrain cycle triggered', 'info'));
}

function runDriftCheck() {
  const model  = document.getElementById('drift-model').value;
  const symbol = document.getElementById('drift-symbol').value;
  if (!model || !symbol) { showToast('Pick a model and symbol first', 'info'); return; }
  postAction('/api/drift-check', {model, symbol});
}

async function loadModelConfidenceOptions() {
  const sel = document.getElementById('drift-model');
  if (!sel) return;
  try {
    const r = await fetch('/api/models');
    const models = await r.json();
    const names = [...new Set(models.map(m => m.model_name))];
    sel.innerHTML = names.length
      ? names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')
      : '<option value="">No models found</option>';
    if (document.getElementById('confidence-symbol').value) loadModelConfidence();
  } catch(e) {}
}

async function loadModelConfidence() {
  const symbol = document.getElementById('confidence-symbol').value;
  const target = document.getElementById('confidence-target').value;
  const out = document.getElementById('confidence-result');
  if (!symbol) { out.innerHTML = '<div class="section-sub" style="margin:0">Pick a symbol.</div>'; return; }
  out.innerHTML = '<div class="section-sub" style="margin:0">Loading…</div>';
  try {
    const r = await fetch('/api/model-confidence?symbol=' + encodeURIComponent(symbol) + '&target=' + target);
    const data = await r.json();
    if (!data.available) {
      out.innerHTML = `<div class="section-sub" style="margin:0">Not available — ${esc(data.reason || 'unknown')}</div>`;
      return;
    }
    let html = `<div class="regime-score">${(data.confidence*100).toFixed(0)}%</div>
      <div class="section-sub" style="text-align:center;margin-bottom:10px">pass-probability (${esc(target)})</div>`;
    if (data.feature_importance) {
      const max = Math.max(...Object.values(data.feature_importance).map(Math.abs), 1e-9);
      for (const [name, val] of Object.entries(data.feature_importance)) {
        const pct = Math.abs(val) / max * 100;
        html += `<div class="feat-row">
          <div class="feat-top"><span style="color:var(--text2)">${esc(name)}</span><span class="num" style="color:var(--accent)">${val.toFixed(3)}</span></div>
          <div class="feat-bar"><div class="feat-fill" style="width:${pct}%"></div></div>
        </div>`;
      }
    }
    out.innerHTML = html;
  } catch(e) {
    out.innerHTML = '<div class="section-sub" style="margin:0">Error loading model confidence.</div>';
  }
}

// ── Job polling + global job indicator ──────────────────────────────────────
function pollJob(jobId, context, onDone) {
  if (pollTimers[jobId]) clearInterval(pollTimers[jobId]);

  activeJobs[jobId] = context;
  renderJobIndicator();

  const logEl    = document.getElementById(context + '-job-log');
  const statusEl = document.getElementById(context + '-job-status');

  pollTimers[jobId] = setInterval(async () => {
    try {
      const r = await fetch('/api/jobs/' + jobId);
      const job = await r.json();

      if (logEl) {
        logEl.textContent = (job.lines || []).join('\n');
        logEl.scrollTop   = logEl.scrollHeight;
      }
      if (statusEl) {
        statusEl.textContent = job.status;
        statusEl.className   = 'job-status job-status-' + job.status;
      }

      if (job.status === 'done' || job.status === 'failed') {
        clearInterval(pollTimers[jobId]);
        delete pollTimers[jobId];
        delete activeJobs[jobId];
        recordJobHistory(context, job.status);
        renderJobIndicator();
        if (onDone) onDone(job);
      }
    } catch(e) { /* network blip — keep polling */ }
  }, 1500);
}

function recordJobHistory(context, status) {
  jobHistory.unshift({context, status, time: new Date().toTimeString().slice(0,5)});
  jobHistory = jobHistory.slice(0, 20);
  renderJobList();
}

function renderJobIndicator() {
  const pill = document.getElementById('job-pill');
  const label = document.getElementById('job-pill-label');
  const contexts = Object.values(activeJobs);
  if (!pill) return;
  if (!contexts.length) { pill.style.display = 'none'; return; }
  pill.style.display = 'flex';
  label.textContent = `${contexts.length} job${contexts.length > 1 ? 's' : ''} running`;
  renderJobList();
}

function renderJobList() {
  const el = document.getElementById('job-list');
  if (!el) return;
  const running = Object.entries(activeJobs).map(([jobId, context]) => ({context, status: 'running', time: '—'}));
  const items = running.concat(jobHistory).slice(0, 12);
  if (!items.length) { el.innerHTML = '<div class="jp-item"><div class="jp-name" style="color:var(--text3)">No jobs yet.</div></div>'; return; }
  el.innerHTML = items.map(j => `
    <div class="jp-item">
      <div class="jp-dot ${j.status === 'running' ? 'jp-run' : j.status === 'done' ? 'jp-done' : 'jp-fail'}"></div>
      <div class="jp-name">${esc(j.context)}</div>
      <span class="jp-tab-link" onclick="jumpToJobTab('${esc(j.context)}')">view</span>
      <div class="jp-time">${esc(j.time)}</div>
    </div>`).join('');
}

function jumpToJobTab(context) {
  const tab = JOB_TAB_MAP[context] || 'data';
  switchTab(tab);
  toggleJobPanel(true);
}

function toggleJobPanel(forceClose) {
  const p = document.getElementById('job-panel');
  if (forceClose) { p.classList.remove('open'); return; }
  p.classList.toggle('open');
  if (p.classList.contains('open')) renderJobList();
}

// ── Elapsed timer ─────────────────────────────────────────────────────────────
function startElapsed(elId) {
  stopElapsed();
  const el = document.getElementById(elId);
  const start = new Date();
  elapsedTimer = setInterval(() => {
    const s = Math.floor((new Date() - start) / 1000);
    const m = Math.floor(s / 60);
    el.textContent = m > 0 ? `${m}m ${s % 60}s elapsed` : `${s}s elapsed`;
  }, 1000);
}

function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  const el = document.getElementById('engine-job-elapsed');
  if (el) el.textContent = '';
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast toast-' + (type || 'info');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add('show')));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
}

// ── Generic confirm modal (resume / live cycle) ─────────────────────────────
function confirmModal({title, body, confirmWord, confirmLabel, onConfirm}) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay open';
  const needsTyping = !!confirmWord;
  overlay.innerHTML = `
    <div class="modal-box">
      <div class="modal-title"><h3>${esc(title)}</h3></div>
      <div class="modal-body">${body}</div>
      ${needsTyping ? `<input type="text" class="modal-input" id="modal-confirm-input" placeholder="Type ${esc(confirmWord)} to confirm" autocomplete="off">` : ''}
      <div class="modal-actions">
        <button class="btn" id="modal-cancel">Cancel</button>
        <button class="btn btn-danger" id="modal-ok">${esc(confirmLabel || 'Confirm')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const input = document.getElementById('modal-confirm-input');
  const okBtn = document.getElementById('modal-ok');
  if (needsTyping) {
    okBtn.disabled = true;
    input.addEventListener('input', () => { okBtn.disabled = input.value !== confirmWord; });
    input.focus();
  }
  document.getElementById('modal-cancel').onclick = () => overlay.remove();
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  okBtn.onclick = () => { overlay.remove(); onConfirm(); };
}

// ── Kill switch / resume / cycle ─────────────────────────────────────────────
function openKillModal() {
  document.getElementById('kill-modal').classList.add('open');
  document.getElementById('halt-input').value = '';
  document.getElementById('halt-btn').disabled = true;
  setTimeout(() => document.getElementById('halt-input').focus(), 80);
}
function closeKillModal() {
  document.getElementById('kill-modal').classList.remove('open');
}
function checkHalt() {
  document.getElementById('halt-btn').disabled = document.getElementById('halt-input').value !== 'HALT';
}
async function executeKill() {
  closeKillModal();
  try {
    const paperR = await fetch('/api/kill-switch', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode:'paper'})});
    const liveR  = await fetch('/api/kill-switch', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode:'live'})});
    const paperData = await paperR.json();
    const liveData  = await liveR.json();
    const closedCount = (paperData.closed||[]).length + (liveData.closed||[]).length;
    showToast(`Kill switch: ${closedCount} position(s) flattened, trading halted`, 'success');
    loadTradingStatus();
  } catch(e) { showToast('Kill switch failed: ' + e, 'error'); }
}

function openResumeModal(exchange, runId) {
  const label = runId ? `<b>${esc(runId)}</b>` : `<b>${esc(exchange)}</b> (every halted slot)`;
  confirmModal({
    title: 'Resume Trading',
    body: `Clear the manual halt for ${label} and allow new opens again.`,
    confirmLabel: 'Resume',
    onConfirm: async () => {
      try {
        const body = {mode: tradingMode, exchange};
        if (runId) body.run_id = runId;
        await fetch('/api/resume', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
        showToast(`${runId || exchange} resumed`, 'success');
        loadTradingStatus();
      } catch(e) { showToast('Resume failed: ' + e, 'error'); }
    },
  });
}

function triggerCycle() {
  const exchange = document.getElementById('cycle-exchange').value;
  if (tradingMode === 'paper') {
    runPaperCycle(exchange);
  } else {
    confirmModal({
      title: 'Run Live Cycle Now',
      body: `This places <b>real orders</b> on <b>${esc(exchange)}</b> if the server has LIVE_TRADING_ENABLED=1 and API keys set — otherwise it logs a shadow run. Type <b>LIVE</b> to confirm.`,
      confirmWord: 'LIVE',
      confirmLabel: 'Run Live Cycle',
      onConfirm: async () => {
        try {
          const r = await fetch('/api/live-cycle', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({exchange})});
          if (r.status === 409) { showToast('Live cycle already running', 'info'); return; }
          const {job_id} = await r.json();
          document.getElementById('live-cycle-job-wrap').style.display = 'block';
          pollJob(job_id, 'live-cycle', (job) => {
            showToast(job.status === 'done' ? 'Live cycle complete' : 'Live cycle failed — check log', job.status === 'done' ? 'success' : 'error');
            loadTradingStatus();
          });
        } catch(e) { showToast('Error: ' + e, 'error'); }
      },
    });
  }
}

async function runPaperCycle(exchange) {
  const btn = document.getElementById('btn-cycle');
  btn.disabled = true;
  try {
    const r = await fetch('/api/paper-cycle', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({exchange})});
    if (r.status === 409) { showToast('Paper cycle already running', 'info'); btn.disabled = false; return; }
    const {job_id} = await r.json();
    document.getElementById('paper-cycle-job-wrap').style.display = 'block';
    pollJob(job_id, 'paper-cycle', (job) => {
      btn.disabled = false;
      showToast(job.status === 'done' ? 'Paper cycle complete' : 'Paper cycle failed — check log', job.status === 'done' ? 'success' : 'error');
      loadTradingStatus();
    });
  } catch(e) {
    btn.disabled = false;
    showToast('Error: ' + e, 'error');
  }
}

function triggerBackfill() {
  const exchange = document.getElementById('cycle-exchange').value;
  confirmModal({
    title: 'Backfill Paper Trading',
    body: `Replay paper trading over the last <b>180 days</b> of already-collected price data on <b>${esc(exchange)}</b>. This resets that exchange's paper slots to a fresh ${esc('$5,000')} bankroll each and writes real historical OPEN/CLOSE rows to the journal.`,
    confirmLabel: 'Backfill',
    onConfirm: () => runPaperBackfill(exchange),
  });
}

async function runPaperBackfill(exchange) {
  const btn = document.getElementById('btn-backfill');
  btn.disabled = true;
  try {
    const r = await fetch('/api/paper-backfill', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({exchange, lookback_days: 180})});
    if (r.status === 409) { showToast('Paper backfill already running', 'info'); btn.disabled = false; return; }
    const {job_id} = await r.json();
    document.getElementById('paper-backfill-job-wrap').style.display = 'block';
    pollJob(job_id, 'paper-backfill', (job) => {
      btn.disabled = false;
      showToast(job.status === 'done' ? 'Backfill complete' : 'Backfill failed — check log', job.status === 'done' ? 'success' : 'error');
      loadTradingStatus();
    });
  } catch(e) {
    btn.disabled = false;
    showToast('Error: ' + e, 'error');
  }
}

async function loadTradingConfig() {
  try {
    const r = await fetch('/api/trading-config');
    const data = await r.json();
    const el = document.getElementById('live-config-badge');
    if (!el) return;
    el.textContent = data.live_trading_enabled ? 'Live trading: server-enabled' : 'Live trading: server-disabled (shadow mode)';
    el.style.color = data.live_trading_enabled ? 'var(--warn)' : 'var(--text3)';
  } catch(e) {}
}

// ── Trading tab ────────────────────────────────────────────────────────────────
function setTradingMode(mode) {
  tradingMode = mode;
  applyModeBadge();
  loadTradingStatus();
}

function toggleStripMode() {
  setTradingMode(tradingMode === 'paper' ? 'live' : 'paper');
}

function applyModeBadge() {
  const badge = document.getElementById('mode-badge');
  const strip = document.getElementById('status-strip');
  if (!badge) return;
  if (tradingMode === 'live') {
    badge.className = 'mode-badge mode-live';
    badge.textContent = 'LIVE';
    strip.style.borderBottom = '2px solid rgba(239,68,68,.6)';
  } else {
    badge.className = 'mode-badge mode-paper';
    badge.textContent = 'PAPER';
    strip.style.borderBottom = '1px solid var(--border)';
  }
}

function renderExchangeCard(exchange, data) {
  const positions = (data && data.positions) || [];
  if (!positions.length) {
    return `<div class="ex-header"><span class="ex-name">${exchange.toUpperCase()}</span><span class="badge-ok">OK</span></div>` +
      `<div style="margin-top:8px;font-size:10px;color:var(--text3)">No open slots yet.</div>`;
  }

  let html = `<div class="ex-header"><span class="ex-name">${exchange.toUpperCase()}</span><span style="font-size:10px;color:var(--text3)">${positions.length} slot${positions.length === 1 ? '' : 's'}</span></div>`;
  html += positions.map(pos => renderPositionRow(exchange, pos)).join('');
  return html;
}

function renderPositionRow(exchange, pos) {
  const sig = pos.current_signal || {};
  const flat = !pos.side || pos.side === 'FLAT';
  const risk = pos.risk || {drawdown_pct_used:0, drawdown_limit_pct:0, daily_loss_pct_used:0, daily_loss_limit_pct:0};
  const halted = !!pos.manual_halt;
  const failed = !!(pos.daily_halt || pos.account_failed);
  const badgeCls = halted || failed ? 'badge-bad' : 'badge-ok';
  const badgeLabel = halted ? 'HALTED' : failed ? 'FAILED' : 'OK';
  const pnlColor = pos.daily_pnl >= 0 ? 'var(--success)' : 'var(--danger)';
  const worstUsed = Math.max(risk.daily_loss_pct_used || 0, risk.drawdown_pct_used || 0);
  const gaugeCls = worstUsed >= 90 ? 'danger' : worstUsed >= 60 ? 'warn' : '';
  const sigName = sig.signal || 'null';

  let html = `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">`;
  html += `<div style="display:flex;justify-content:space-between;align-items:baseline">`;
  html += `<span style="font-size:10px;color:var(--text3)" title="${esc(pos.run_id)}">${esc(pos.strategy_name || '?')}</span>`;
  html += `<span class="${badgeCls}">${badgeLabel}</span>`;
  html += `</div>`;
  html += `<div class="num" style="font-size:15px;font-weight:700;color:${pnlColor};margin:2px 0 6px">${pos.daily_pnl >= 0 ? '+' : ''}$${Number(pos.daily_pnl).toFixed(2)}</div>`;
  html += `<div class="gauge-meta"><span>Risk used</span><span>${worstUsed.toFixed(0)}% of limit</span></div>`;
  html += `<div class="gauge-bar"><div class="gauge-fill ${gaugeCls}" style="width:${Math.min(100, worstUsed)}%"></div></div>`;
  html += `<div style="margin-top:8px;font-size:10px;color:var(--text3);display:flex;align-items:center;gap:6px;flex-wrap:wrap">`;
  html += flat ? '<span>FLAT</span>' : `<span>${esc(pos.side)} ${esc(pos.symbol)}</span>`;
  html += `<span class="signal-badge signal-${esc(sigName)}">${esc(sig.signal || 'NO SIGNAL')}</span>`;
  html += `</div>`;
  if (halted) {
    html += `<div style="margin-top:8px"><button class="export-btn" onclick="openResumeModal('${esc(exchange)}', '${esc(pos.run_id)}')"><i class="ti ti-player-play"></i> Resume</button></div>`;
  }
  html += `</div>`;
  return html;
}

// ── Trade journal ─────────────────────────────────────────────────────────────
function relativeDateToISO(rel) {
  const now = new Date();
  let start;
  if (rel === 'today') start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  else if (rel === 'week') { start = new Date(now); start.setDate(start.getDate() - 7); }
  else if (rel === 'month') { start = new Date(now); start.setMonth(start.getMonth() - 1); }
  else return null;
  return start.toISOString().slice(0, 10);
}

async function loadJournalSummary() {
  const tbody = document.getElementById('journal-body');
  const params = new URLSearchParams();
  const exchange = document.getElementById('jf-exchange')?.value;
  const mode = document.getElementById('jf-mode')?.value;
  const outcome = document.getElementById('jf-outcome')?.value;
  const dateRel = document.getElementById('jf-date')?.value;
  if (exchange) params.set('exchange', exchange);
  if (mode) params.set('mode', mode);
  if (outcome) params.set('outcome', outcome);
  const startISO = relativeDateToISO(dateRel);
  if (startISO) params.set('start', startISO);

  try {
    const r = await fetch('/api/trade-journal-summary?' + params.toString());
    const rows = await r.json();
    if (rows.error) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="5">Error: ${esc(rows.error)}</td></tr>`;
      lastJournalRows = [];
      return;
    }
    lastJournalRows = rows;
    renderJournalRows();
  } catch(e) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">Error loading journal summary.</td></tr>';
    lastJournalRows = [];
  }
}

function filteredJournalRows() {
  const q = (document.getElementById('jf-search')?.value || '').toLowerCase();
  if (!q) return lastJournalRows;
  return lastJournalRows.filter(r => r.setup_tag.toLowerCase().includes(q));
}

function renderJournalRows() {
  const tbody = document.getElementById('journal-body');
  const rows = filteredJournalRows();
  if (!rows.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No closed trades match these filters.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="tag tag-cyan">${esc(r.setup_tag)}</span></td>
      <td class="num">${r.n_trades}</td>
      <td class="${r.win_rate_pct >= 50 ? 'pos' : 'neg'} num">${r.win_rate_pct}%</td>
      <td class="${r.avg_pnl >= 0 ? 'pos' : 'neg'} num">${r.avg_pnl.toFixed(2)}</td>
      <td class="${r.total_pnl >= 0 ? 'pos' : 'neg'} num">${r.total_pnl.toFixed(2)}</td>
    </tr>`).join('');
}

function exportJournalCSV() {
  const rows = filteredJournalRows();
  if (!rows.length) { showToast('Nothing to export', 'info'); return; }
  const header = ['setup_tag', 'n_trades', 'win_rate_pct', 'avg_pnl', 'total_pnl'];
  const lines = [header.join(',')].concat(rows.map(r => header.map(h => r[h]).join(',')));
  const blob = new Blob([lines.join('\n')], {type: 'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'trade-journal-summary.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// ── Recent orders (scoped to the chart-exchange / chart-run selectors) ──────
function currentChartRunId() {
  return document.getElementById('chart-run').value || null;
}

function positionsForExchange(exchange, source) {
  const d = (source || lastTradingData);
  return (d && d[exchange] && d[exchange].positions) || [];
}

function populateChartRunSelector(data) {
  const exchange = document.getElementById('chart-exchange').value;
  const sel = document.getElementById('chart-run');
  const positions = positionsForExchange(exchange, data);
  const prev = sel.value;
  if (!positions.length) {
    sel.innerHTML = '<option value="">(no open slots)</option>';
    return;
  }
  sel.innerHTML = positions.map(p =>
    `<option value="${esc(p.run_id)}">${esc(p.strategy_name || '?')} · ${esc(p.symbol || '?')}</option>`
  ).join('');
  if (positions.some(p => p.run_id === prev)) sel.value = prev;
}

function renderOrders(data) {
  const exchange = document.getElementById('chart-exchange').value;
  const label = document.getElementById('orders-exchange-label');
  if (label) label.textContent = exchange;
  const runId = currentChartRunId();
  const positions = positionsForExchange(exchange, data);
  const pos = runId ? positions.find(p => p.run_id === runId) : positions[0];
  const orders = (pos && pos.recent_orders) || [];
  const tbody = document.getElementById('orders-body');
  if (!orders.length) { tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No orders yet.</td></tr>'; return; }
  tbody.innerHTML = orders.map(o => `
    <tr>
      <td>${esc(o.event)}</td><td>${esc(o.side)}</td>
      <td class="num">${Number(o.qty).toFixed(6)}</td>
      <td class="num">${Number(o.price).toFixed(4)}</td>
      <td class="num ${o.pnl >= 0 ? 'pos' : 'neg'}">${o.pnl != null ? Number(o.pnl).toFixed(2) : '-'}</td>
      <td style="color:var(--text3);font-size:10px">${esc(o.setup_tag || o.close_reason || '-')}</td>
      <td class="num" style="font-size:10px">${esc(o.created_at)}</td>
    </tr>`).join('');
}

function onChartExchangeChange() {
  populateChartRunSelector();
  renderOrders();
  loadTradingChart();
  loadEquityCurve();
}

// ── Global risk strip + alerts ───────────────────────────────────────────────
async function fetchTradingStatusData() {
  const r = await fetch('/api/trading-status?mode=' + tradingMode);
  return r.json();
}

async function refreshGlobalRiskStrip() {
  try {
    const data = await fetchTradingStatusData();
    lastTradingData = data;
    updateRiskStrip(data);
    checkRiskAlerts(data);
  } catch(e) { /* non-fatal — strip just stays at last known state */ }
}

function updateRiskStrip(data) {
  let exposure = 0, dailyPnl = 0, worstRiskPct = 0, anyHalt = false;
  for (const ex of EXCHANGES) {
    const positions = (data[ex] && data[ex].positions) || [];
    for (const pos of positions) {
      exposure += Number(pos.equity || 0);
      dailyPnl += Number(pos.daily_pnl || 0);
      const r = pos.risk || {};
      worstRiskPct = Math.max(worstRiskPct, r.daily_loss_pct_used || 0, r.drawdown_pct_used || 0);
      if (pos.manual_halt || pos.daily_halt || pos.account_failed) anyHalt = true;
    }
  }
  const expEl = document.getElementById('ss-exposure');
  const pnlEl = document.getElementById('ss-pnl');
  const riskEl = document.getElementById('ss-risk');
  const statusEl = document.getElementById('ss-status');
  if (expEl) expEl.textContent = '$' + exposure.toFixed(2);
  if (pnlEl) {
    pnlEl.textContent = (dailyPnl >= 0 ? '+' : '') + '$' + dailyPnl.toFixed(2);
    pnlEl.className = 'ss-val num ' + (dailyPnl >= 0 ? 'pos' : 'neg');
  }
  if (riskEl) {
    riskEl.textContent = worstRiskPct.toFixed(0) + '%';
    riskEl.style.color = worstRiskPct >= 90 ? 'var(--danger)' : worstRiskPct >= 60 ? 'var(--warn)' : 'var(--success)';
  }
  if (statusEl) {
    statusEl.textContent = anyHalt ? 'HALTED' : 'NOMINAL';
    statusEl.style.color = anyHalt ? 'var(--danger)' : 'var(--success)';
  }
  applyModeBadge();
}

function checkRiskAlerts(data) {
  for (const ex of EXCHANGES) {
    const positions = (data[ex] && data[ex].positions) || [];
    for (const pos of positions) {
      const runId = pos.run_id || ex;
      const st = alertState[runId] = alertState[runId] || {};
      const risk = pos.risk || {};

      const halted = !!pos.manual_halt;
      if (halted && !st.haltWarned) {
        showToast(`${runId} HALTED — manual kill switch active`, 'error');
        st.haltWarned = true;
      } else if (!halted) { st.haltWarned = false; }

      const failed = !!(pos.daily_halt || pos.account_failed);
      if (failed && !st.failWarned) {
        showToast(`${runId} ${pos.account_failed ? 'account failed' : 'daily loss limit hit'} — trading halted`, 'error');
        st.failWarned = true;
      } else if (!failed) { st.failWarned = false; }

      const worstUsed = Math.max(risk.daily_loss_pct_used || 0, risk.drawdown_pct_used || 0);
      if (worstUsed >= 90 && !st.riskWarned) {
        showToast(`${runId} risk usage at ${worstUsed.toFixed(0)}% of limit`, 'error');
        st.riskWarned = true;
      } else if (worstUsed < 80) { st.riskWarned = false; }
    }
  }
}

function updateTradingMetrics(data) {
  let equity = 0, openCount = 0, orderCount = 0, worstRisk = 0;
  for (const ex of EXCHANGES) {
    const d = data[ex];
    if (!d) continue;
    for (const pos of (d.positions || [])) {
      equity += Number(pos.equity || 0);
      if (pos.side && pos.side !== 'FLAT') openCount += 1;
      orderCount += (pos.recent_orders || []).length;
      const r = pos.risk || {};
      worstRisk = Math.max(worstRisk, r.daily_loss_pct_used || 0, r.drawdown_pct_used || 0);
    }
  }
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('m-equity', '$' + equity.toFixed(2));
  set('m-positions', String(openCount));
  set('m-orders', String(orderCount));
  set('m-risk', worstRisk.toFixed(0) + '%');
}

async function loadTradingStatus() {
  try {
    const data = await fetchTradingStatusData();
    lastTradingData = data;
    document.getElementById('ex-binance').innerHTML = renderExchangeCard('binance', data.binance);
    document.getElementById('ex-kraken').innerHTML = renderExchangeCard('kraken', data.kraken);
    document.getElementById('ex-htx').innerHTML = renderExchangeCard('htx', data.htx);
    updateTradingMetrics(data);
    updateRiskStrip(data);
    checkRiskAlerts(data);
    populateChartRunSelector(data);
    renderOrders(data);
    loadTradingChart();
    loadJournalSummary();
    loadEquityCurve();
  } catch (e) { showToast('Could not load trading status', 'error'); }
}

async function loadTradingChart() {
  const exchange = document.getElementById('chart-exchange').value;
  const runId = currentChartRunId();
  try {
    const url = '/api/trading-chart?exchange=' + exchange + '&mode=' + tradingMode +
      (runId ? '&run_id=' + encodeURIComponent(runId) : '');
    const r = await fetch(url);
    const data = await r.json();
    const bars = data.bars || [];
    const trades = data.trades || [];

    const candle = {
      type: 'candlestick',
      x: bars.map(b => b.timestamp),
      open: bars.map(b => b.open), high: bars.map(b => b.high),
      low: bars.map(b => b.low), close: bars.map(b => b.close),
      name: data.symbol || exchange,
      increasing: {line: {color: '#22c55e'}}, decreasing: {line: {color: '#ef4444'}},
    };

    const opens = trades.filter(t => t.event === 'OPEN');
    const closes = trades.filter(t => t.event === 'CLOSE');
    const openMarker = {
      type: 'scatter', mode: 'markers', name: 'OPEN',
      x: opens.map(t => t.created_at), y: opens.map(t => t.price),
      marker: {symbol: 'triangle-up', size: 11, color: '#00d4ff'},
    };
    const closeMarker = {
      type: 'scatter', mode: 'markers', name: 'CLOSE',
      x: closes.map(t => t.created_at), y: closes.map(t => t.price),
      marker: {symbol: 'triangle-down', size: 11, color: '#f59e0b'},
    };

    Plotly.newPlot('trading-chart', [candle, openMarker, closeMarker], {
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      font: {color: '#e2e8f0'},
      margin: {t: 10, r: 10, l: 50, b: 30},
      xaxis: {rangeslider: {visible: false}, gridcolor: 'rgba(255,255,255,.08)'},
      yaxis: {gridcolor: 'rgba(255,255,255,.08)'},
      showlegend: true,
      legend: {orientation: 'h'},
    }, {responsive: true, displayModeBar: false});
  } catch (e) { showToast('Could not load chart', 'error'); }
}

async function loadEquityCurve() {
  const exchange = document.getElementById('chart-exchange').value;
  const runId = currentChartRunId();
  const el = document.getElementById('equity-curve-chart');
  if (!el) return;
  try {
    const url = '/api/equity-curve?exchange=' + exchange + '&mode=' + tradingMode +
      (runId ? '&run_id=' + encodeURIComponent(runId) : '');
    const r = await fetch(url);
    const data = await r.json();
    const eq = data.equity || [];
    const bench = data.benchmark || [];
    if (!eq.length) {
      el.innerHTML = '<div class="section-sub" style="margin:0">No closed trades yet — equity curve will appear once trades close.</div>';
      return;
    }
    el.innerHTML = '';
    Plotly.newPlot(el, [
      {type:'scatter', mode:'lines', name:'Equity', x: eq.map(p=>p.timestamp), y: eq.map(p=>p.equity), line:{color:'#00d4ff', width:2}},
      {type:'scatter', mode:'lines', name:'Buy & Hold', x: bench.map(p=>p.timestamp), y: bench.map(p=>p.equity), line:{color:'#64748b', width:1, dash:'dash'}},
    ], {
      paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
      font: {color: '#e2e8f0'},
      margin: {t: 10, r: 10, l: 50, b: 30},
      xaxis: {gridcolor: 'rgba(255,255,255,.08)'},
      yaxis: {gridcolor: 'rgba(255,255,255,.08)'},
      showlegend: true,
      legend: {orientation: 'h'},
    }, {responsive: true, displayModeBar: false});
  } catch(e) { /* non-fatal */ }
}
