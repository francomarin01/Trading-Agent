const $ = id => document.getElementById(id);
const staticDeployment = document.documentElement.dataset.deployment === 'static';
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const f = (n, d = 2) => n == null ? '—' : Number(n).toFixed(d);
const p = n => n == null ? '—' : `${Number(n).toFixed(2)}%`;
let data = {assets: [], rotation_history: [], asset_rotation_history: [], track_record: [], track_record_sector_summary: [], journal: [], journal_summary: [], watchlist: [], top10: [], top10_summary: []};
let rotationDates = [];
let selectedTicker = null;
let playback = null;
let loadedRefreshAttempt = null;
let activeKpi = 'all';
let assetRotationLoaded = false;
let assetRotationLoading = null;

const row = values => `<tr>${values.map(value => `<td>${value}</td>`).join('')}</tr>`;
const alerts = asset => (asset.alerts || []).join('; ');
const stateBadge = state => `<span class="sector-state ${state}">${state}</span>`;
const operationalBadge = asset => {
  if (asset.eligible_for_new_entry) return '<span class="operational NEW_ENTRY">NUEVA ENTRADA</span>';
  if (asset.selective_setup_candidate) return '<span class="operational SELECTIVE_SETUP">SELECTIVE SETUP</span>';
  if (asset.score_cross_65) return '<span class="operational WAIT_GATE">ESPERA GATE</span>';
  return '<span class="operational REVIEW">SEGUIMIENTO</span>';
};

function gate() { return data.market?.operational_gate || {status: 'DATA UNAVAILABLE', reasons: ['Gate no calculado']}; }

function renderOperationalGate() {
  const value = gate();
  const condition = (label, pass, detail) => `<div class="gate-condition ${pass === true ? 'pass' : pass === false ? 'fail' : 'unknown'}"><span>${label}</span><b>${pass === true ? 'OK' : pass === false ? 'BLOQUEA' : 'SIN DATO'}</b><small>${detail}</small></div>`;
  const trend = value.spy_trend === undefined || value.qqq_trend === undefined ? undefined : value.spy_trend && value.qqq_trend;
  const breadth = value.breadth_pct == null ? undefined : value.breadth_pct > 50;
  const vix = value.vix_pass;
  const putCall = value.put_call_pass;
  const status = value.status === 'FULL' ? 'FULL · NUEVAS ENTRADAS HABILITADAS' : value.status === 'BLOCKED' ? 'BLOQUEADO · SIN NUEVAS ENTRADAS' : 'DATA UNAVAILABLE · SIN NUEVAS ENTRADAS';
  const note = value.status === 'FULL' ? 'El gate habilita sólo señales nuevas que cruzan score 65 al cierre.' : `Motivos: ${(value.reasons || ['Datos de gate incompletos']).join(', ')}.`;
  $('operationalGate').innerHTML = `<div class="gate-summary ${value.status}"><div><span class="section-kicker">GATE OPERATIVO</span><h2>${status}</h2><p>${note}</p></div><span>${value.as_of || data.as_of || '—'}</span></div><div class="gate-grid">${condition('SPY + QQQ', trend, 'Close > SMA50 > SMA200')}${condition('Breadth', breadth, value.breadth_pct == null ? 'DATA UNAVAILABLE' : `${f(value.breadth_pct, 1)}% · ${value.breadth_valid_count || 0} activos`)}${condition('VIX', vix, value.vix == null ? 'DATA UNAVAILABLE' : `${f(value.vix, 2)} · límite <22`)}${condition('Put/Call TOTAL', putCall, value.put_call == null ? 'DATA UNAVAILABLE' : `${f(value.put_call, 3)} · ${value.put_call_source_date || '—'}`)}</div>`;
}

function renderTop10() {
  const rows = data.top10 || [];
  $('top10NewEntries').textContent = `${data.top10_new_entries || 0} ingresos nuevos en esta actualización`;
  $('top10Summary').innerHTML = (data.top10_summary || []).map(item => `<div class="top10-metric"><span>Top 10 · ${item.horizon_sessions} ruedas</span><b>${p(item.average_return_pct)}</b><small>${item.valid} con retorno válido · WR ${item.win_rate_pct == null ? '—' : `${f(item.win_rate_pct)}%`} · Exceso SPY ${p(item.average_excess_spy_pct)}</small></div>`).join('') || '<div class="empty">Aún no hay suficiente historia prospectiva del Top 10.</div>';
  $('top10Rows').innerHTML = rows.map(item => row([
    `<b class="rank-number">${item.rank}</b>`, `<button class="ticker" data-top10-ticker="${item.ticker}">${item.ticker}</button>`, item.sector_etf, `<b>${f(item.score)}</b>`,
    `<span class="state ${String(item.state).replaceAll(' ', '_')}">${item.state}</span>`, item.signal_date || 'DATA UNAVAILABLE', item.entry_date || 'PENDIENTE T+1',
    p(item.current_return_pct), p(item.return_5d_pct), p(item.return_10d_pct), p(item.return_20d_pct), p(item.excess_spy_20d_pct),
  ])).join('') || row(['DATA UNAVAILABLE', '', '', '', '', '', '', '', '', '', '', '']);
  document.querySelectorAll('[data-top10-ticker]').forEach(button => button.onclick = () => {
    const asset = data.assets.find(item => item.ticker === button.dataset.top10Ticker);
    if (asset) { detail(asset); activateTab('universe'); }
  });
}

function renderSelectiveSetup() {
  const candidates = data.assets.filter(asset => asset.selective_setup_candidate);
  $('selectiveCount').textContent = `${candidates.length} candidatas`;
  $('selectiveRows').innerHTML = candidates.map(asset => row([`<button class="ticker" data-ticker="${asset.ticker}">${asset.ticker}</button>`, asset.sector_etf, `<b>${f(asset.selective_score_25_30_30_15)}</b>`, asset.selective_setup_reason || '—', f(asset.score), `<span class="state ${asset.state.replace(' ', '_')}">${asset.state}</span>`])).join('') || row([gate().status === 'FULL' ? 'No hay SELECTIVE SETUP al último cierre.' : 'Gate no habilitado; no se emiten candidatas.', '', '', '', '', '']);
}

function bindTickerButtons() {
  document.querySelectorAll('[data-ticker]').forEach(button => button.onclick = () => {
    const asset = data.assets.find(item => item.ticker === button.dataset.ticker);
    if (asset) { detail(asset); activateTab('opportunities'); }
  });
}

async function watchlistAction(path, method, ticker, payload = {}) {
  if (staticDeployment) {
    window.alert('La versión publicada es de solo lectura. La watchlist se administra desde la instancia local.');
    return;
  }
  const buttons = [...document.querySelectorAll('[data-watch-action]')];
  buttons.forEach(button => { button.disabled = true; });
  try {
    const response = await fetch(path, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: method === 'DELETE' ? undefined : JSON.stringify({ticker, ...payload}),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'No se pudo actualizar la watchlist');
    window.location.reload();
  } catch (error) {
    window.alert(error.message || 'No se pudo actualizar la watchlist');
    buttons.forEach(button => { button.disabled = false; });
  }
}

let executionTicker = null;

function updateExecutionPreview() {
  const asset = data.assets.find(item => item.ticker === executionTicker);
  const price = Number($('executionPrice').value);
  const atr = Number(asset?.atr14);
  if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(atr) || atr <= 0) {
    $('executionPreview').textContent = 'Ingresá un precio válido para calcular el riesgo.';
    return;
  }
  const risk = 2 * atr;
  $('executionPreview').innerHTML = `ATR14 congelado: <b>${f(atr)}</b> · Riesgo por acción: <b>${f(risk)}</b> (${f(100 * risk / price)}%) · Stop: <b>${f(price - risk)}</b> · Target 2R: <b>${f(price + 2 * risk)}</b>`;
}

function openExecutionDialog(ticker) {
  const asset = data.assets.find(item => item.ticker === ticker);
  if (!asset) return;
  executionTicker = ticker;
  $('executionTicker').value = ticker;
  $('executionDate').value = data.as_of || new Date().toISOString().slice(0, 10);
  $('executionPrice').value = asset.close == null ? '' : f(asset.close);
  $('executionError').textContent = '';
  updateExecutionPreview();
  $('executionDialog').showModal();
}

function setupExecutionDialog() {
  $('executionPrice').addEventListener('input', updateExecutionPreview);
  $('executionDialog').addEventListener('close', () => { executionTicker = null; });
  $('executionForm').addEventListener('submit', event => {
    event.preventDefault();
    const entryPrice = Number($('executionPrice').value);
    const entryDate = $('executionDate').value;
    if (!executionTicker || !Number.isFinite(entryPrice) || entryPrice <= 0 || !entryDate) {
      $('executionError').textContent = 'Ingresá una fecha y un precio de ejecución válidos.';
      return;
    }
    $('executionDialog').close();
    watchlistAction('/api/watchlist/open', 'POST', executionTicker, {entry_price: entryPrice, entry_date: entryDate});
  });
}

function renderWatchlist() {
  const rows = data.watchlist || [];
  $('watchlistCount').textContent = `${rows.length} activo${rows.length === 1 ? '' : 's'}`;
  $('watchlistRows').innerHTML = rows.map(item => {
    const status = item.position_status || 'WATCHING';
    const action = !staticDeployment && (status === 'WATCHING' || status === 'PENDING_ENTRY')
      ? `<button class="watchlist-action primary" data-watch-open="${item.ticker}" data-watch-action>Registrar entrada real</button>`
      : `<span class="muted">${staticDeployment ? 'Solo lectura' : 'Seguimiento iniciado'}</span>`;
    return row([
      `<button class="ticker" data-ticker="${item.ticker}">${item.ticker}</button>`, item.sector_etf, item.added_date,
      f(item.score), `<span class="state ${String(item.state || 'DATA UNAVAILABLE').replaceAll(' ', '_')}">${item.state || 'DATA UNAVAILABLE'}</span>`,
      `<span class="operational ${String(status).replaceAll(' ', '_')}">${status}</span>`, item.entry_date || 'SIN EJECUCIÓN',
      p(item.current_return_pct), f(item.stop_price), f(item.target_price),
      `<div class="watchlist-buttons">${action}${staticDeployment ? '' : `<button class="watchlist-action" data-watch-remove="${item.ticker}" data-watch-action>Quitar</button>`}</div>`,
    ]);
  }).join('') || row(['Todavía no agregaste activos a tu watchlist.', '', '', '', '', '', '', '', '', '', '']);
  document.querySelectorAll('[data-watch-open]').forEach(button => button.onclick = () => openExecutionDialog(button.dataset.watchOpen));
  document.querySelectorAll('[data-watch-remove]').forEach(button => button.onclick = () => watchlistAction(`/api/watchlist/${encodeURIComponent(button.dataset.watchRemove)}`, 'DELETE', button.dataset.watchRemove));
  bindTickerButtons();
}

function renderJournal() {
  const labels = {CURRENT_GATE: 'Score actual + gate', SELECTIVE_SETUP: 'SELECTIVE SETUP', MANUAL_WATCHLIST: 'Watchlist manual'};
  $('journalNewSignals').textContent = `${data.journal_new_signals || 0} señales nuevas en esta actualización`;
  $('journalSummaryRows').innerHTML = (data.journal_summary || []).map(item => row([
    labels[item.model] || item.model, item.signals, item.active, item.pending_entry, item.matured_20d,
    `${f(item.win_rate_20d_pct)}%`, p(item.average_return_20d_pct), p(item.average_excess_spy_20d_pct),
  ])).join('') || row(['Aún no hay señales prospectivas registradas.', '', '', '', '', '', '', '']);
  $('journalRows').innerHTML = (data.journal || []).map(item => row([
    labels[item.model] || item.model, `<button class="ticker" data-ticker="${item.ticker}">${item.ticker}</button>`, item.sector_etf,
    item.signal_date, item.entry_date || 'PENDIENTE', f(item.signal_score), `<span class="operational ${item.status}">${item.status}</span>`,
    f(item.stop_price), f(item.target_price), item.r_multiple_to_date == null ? '—' : `${f(item.r_multiple_to_date)}R`, `<span class="operational ${String(item.risk_status || '').replaceAll(' ', '_')}">${item.risk_status || '—'}</span>`,
    p(item.current_return_pct), p(item.excess_vs_spy_pct), p(item.return_5d_pct), p(item.return_10d_pct), p(item.return_20d_pct), p(item.excess_spy_20d_pct),
  ])).join('') || row(['Aún no hay señales prospectivas registradas. Se crearán cuando haya un cruce ≥65 y gate FULL.', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '']);
  bindTickerButtons();
}

function matchesKpi(asset) {
  if (activeKpi === 'ema200') return asset.close >= asset.ema200;
  if (activeKpi === 'sma50') return asset.close >= asset.sma50;
  if (activeKpi === 'rs') return (asset.rs_score ?? -1) >= 80;
  if (activeKpi === 'volume') return asset.unusual_volume;
  return true;
}

function renderQuickKpis(rows) {
  const metrics = [
    ['all', 'Candidatas', rows.length, 'con los filtros actuales'],
    ['ema200', 'Sobre EMA200', rows.filter(asset => asset.close >= asset.ema200).length, 'tendencia estructural'],
    ['sma50', 'Sobre SMA50', rows.filter(asset => asset.close >= asset.sma50).length, 'estructura intermedia'],
    ['rs', 'RS alto', rows.filter(asset => (asset.rs_score ?? -1) >= 80).length, 'RS Score ≥80'],
    ['volume', 'Volumen inusual', rows.filter(asset => asset.unusual_volume).length, '≥1.5x SMA50'],
  ];
  $('quickKpis').innerHTML = metrics.map(([key, label, value, note]) => `<button type="button" class="quick-kpi ${activeKpi === key ? 'active' : ''}" data-kpi="${key}"><span>${label}</span><b>${value}</b><small>${note}</small></button>`).join('');
  document.querySelectorAll('[data-kpi]').forEach(button => button.onclick = () => { activeKpi = button.dataset.kpi; drawAssets(); });
}

function trendChart(series) {
  if (!series || series.length < 2) return '<div class="chart-empty">Tendencia no disponible.</div>';
  const width = 270, height = 116, padding = 6;
  const values = series.flatMap(x => [x.close, x.ema9, x.ema21, x.sma50, x.ema200]).filter(Number.isFinite);
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const point = (value, index) => `${padding + index * (width - padding * 2) / (series.length - 1)},${height - padding - (value - min) * (height - padding * 2) / span}`;
  const path = key => series.filter(x => Number.isFinite(x[key])).map((x, index) => `${index ? 'L' : 'M'}${point(x[key], index)}`).join(' ');
  return `<svg class="trend-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Tendencia de cierre y medias móviles"><path class="ema200-line" d="${path('ema200')}"/><path class="sma50-line" d="${path('sma50')}"/><path class="close-line" d="${path('close')}"/><path class="ema9-line" d="${path('ema9')}"/><path class="ema21-line" d="${path('ema21')}"/></svg><div class="chart-legend"><span class="close-key">Cierre</span><span class="ema9-key">EMA9</span><span class="ema21-key">EMA21</span><span class="sma50-key">SMA50</span><span class="ema200-key">EMA200</span><span>${series[0].date} → ${series.at(-1).date}</span></div>`;
}

function detail(asset) {
  if (!asset) return;
  selectedTicker = asset.ticker;
  const pillars = asset.pillars || {};
  const setup = asset.setup_event ? `${asset.setup_event}${asset.setup_age_sessions != null ? ` · ${asset.setup_age_sessions} ruedas` : ''}` : 'Sin evento reciente';
  const detectorText = (asset.detectors || []).map(item => `<li><b>${item.name}</b> · ${item.date}<br><span>${item.detail}</span></li>`).join('') || '<li>Sin detectores activos al último cierre.</li>';
  const watched = (data.watchlist || []).find(item => item.ticker === asset.ticker);
  const watchAction = watched
    ? (watched.position_status === 'WATCHING'
      ? `<button class="watchlist-action primary" data-detail-watch-open="${asset.ticker}" data-watch-action>Registrar entrada real</button>`
      : `<span class="muted">En watchlist · ${watched.position_status}</span>`)
    : `<button class="watchlist-action primary" data-detail-watch-add="${asset.ticker}" data-watch-action>+ Agregar a watchlist</button>`;
  $('assetDetail').innerHTML = `<div class="eyebrow">${asset.sector_etf} · ${asset.state}</div><h2>${asset.ticker}</h2>${operationalBadge(asset)}<div class="manual-actions">${watchAction}</div><div class="big-score">${f(asset.opportunity_score)}<span>score ajustado</span></div><div class="detail-grid"><div><span>Precio / día</span><b>${f(asset.close)} · ${p(asset.daily_return_pct)}</b></div><div><span>Score cuatro pilares</span><b>${f(asset.score)}</b></div><div><span>Score selectivo 25/30/30/15</span><b>${f(asset.selective_score_25_30_30_15)}</b></div><div><span>RS compuesto</span><b>${f(asset.rs_score, 1)}/100 · ${p(asset.rs_change_5d_pct)}</b></div><div><span>Estadio técnico</span><b>${asset.technical_stage || '—'} · ${p(asset.ema200_slope20_pct)}</b></div><div><span>Dist. SMA50 / EMA200</span><b>${p(asset.distance_sma50_pct)} · ${p(asset.distance_ema200_pct)}</b></div><div><span>ATR14 / volatilidad</span><b>${f(asset.atr14)} · ${p(asset.atr14_pct)}</b></div><div><span>Setup</span><b class="${asset.fresh_setup ? 'fresh-setup' : ''}">${setup}</b></div><div><span>Setup selectivo</span><b class="${asset.selective_setup ? 'fresh-setup' : ''}">${asset.selective_setup_reason || 'No confirmado'}</b></div><div><span>Volumen</span><b>${f(asset.volume_ratio50)}x</b></div><div><span>Earnings</span><b>DATA UNAVAILABLE</b></div></div><h3>Cuatro pilares</h3><div class="pillar-grid"><div><span>Tendencia</span><b>${f(pillars.trend)}</b><i style="width:${Math.min(100, Number(pillars.trend || 0) / 30 * 100)}%"></i></div><div><span>Fuerza RS</span><b>${f(pillars.relative_strength)}</b><i style="width:${Math.min(100, Number(pillars.relative_strength || 0) / 25 * 100)}%"></i></div><div><span>Contracción</span><b>${f(pillars.contraction)}</b><i style="width:${Math.min(100, Number(pillars.contraction || 0) / 20 * 100)}%"></i></div><div><span>Setup</span><b>${f(pillars.setup)}</b><i style="width:${Math.min(100, Number(pillars.setup || 0) / 25 * 100)}%"></i></div></div><h3>Tendencia · 60 ruedas</h3>${trendChart(asset.trend_60)}<h3>Detectores independientes</h3><ul class="detectors">${detectorText}</ul><h3>Condiciones a revisar</h3><p class="alerts">${alerts(asset) || 'Sin alertas activas'}</p>`;
  if (staticDeployment) $('assetDetail').querySelector('.manual-actions').innerHTML = '<span class="muted">Watchlist disponible en la instancia local.</span>';
  const add = document.querySelector('[data-detail-watch-add]');
  const open = document.querySelector('[data-detail-watch-open]');
  if (add) add.onclick = () => watchlistAction('/api/watchlist', 'POST', add.dataset.detailWatchAdd);
  if (open) open.onclick = () => openExecutionDialog(open.dataset.detailWatchOpen);
}

function renderSpotlight(rows) {
  const top = [...rows].sort((a, b) => b.opportunity_score - a.opportunity_score).slice(0, 3);
  $('spotlight').innerHTML = top.map((asset, index) => {
    const pillars = asset.pillars || {};
    return `<button type="button" class="spotlight-card" data-spotlight="${asset.ticker}"><span class="spotlight-rank">#${index + 1}</span><b>${asset.ticker}</b><small>${asset.sector_etf}</small><strong>${f(asset.opportunity_score, 0)}</strong><em>score ajustado</em><div><span>T ${f(pillars.trend, 0)}</span><span>RS ${f(pillars.relative_strength, 0)}</span><span>C ${f(pillars.contraction, 0)}</span><span>S ${f(pillars.setup, 0)}</span></div></button>`;
  }).join('') || '<div class="empty">No hay activos con los filtros actuales.</div>';
  document.querySelectorAll('[data-spotlight]').forEach(button => button.onclick = () => detail(data.assets.find(asset => asset.ticker === button.dataset.spotlight)));
}

function renderHeatmap() {
  const sorted = [...data.assets].sort((a, b) => Number(b.daily_return_pct || 0) - Number(a.daily_return_pct || 0));
  $('heatmap').innerHTML = sorted.map(asset => {
    const daily = Number(asset.daily_return_pct || 0);
    const type = daily > .15 ? 'gain' : daily < -.15 ? 'loss' : 'flat';
    return `<button type="button" class="heat-tile ${type}" data-heat="${asset.ticker}" title="${asset.ticker} · ${asset.sector_etf} · ${p(daily)}"><b>${asset.ticker}</b><span>${p(daily)}</span><small>${asset.sector_etf}</small></button>`;
  }).join('');
  document.querySelectorAll('[data-heat]').forEach(button => button.onclick = () => { detail(data.assets.find(asset => asset.ticker === button.dataset.heat)); activateTab('opportunities'); });
}

function rsRows(ticker, sessions) {
  return data.asset_rotation_history.filter(item => item.ticker === ticker).slice(-sessions);
}

function rsChart(seriesByTicker, label) {
  const present = Object.values(seriesByTicker).filter(series => series.length > 1);
  if (!present.length) return '<div class="chart-empty">DATA UNAVAILABLE · No hay historia RS suficiente.</div>';
  const width = 760, height = 220, pad = 24;
  const palette = ['#2764eb', '#14a66a', '#e39a16', '#d04f55'];
  const maxLength = Math.max(...present.map(series => series.length));
  const point = (value, index, count) => `${pad + index * (width - pad * 2) / Math.max(1, count - 1)},${height - pad - value * (height - pad * 2) / 100}`;
  const paths = Object.entries(seriesByTicker).map(([ticker, series], index) => {
    if (series.length < 2) return '';
    const path = series.map((item, position) => `${position ? 'L' : 'M'}${point(Number(item.rs_score || 0), position, series.length)}`).join(' ');
    return `<path d="${path}" style="stroke:${palette[index % palette.length]}"></path>`;
  }).join('');
  const legend = Object.keys(seriesByTicker).map((ticker, index) => `<span style="--series:${palette[index % palette.length]}">${ticker}</span>`).join('');
  const first = present[0][0]?.date, last = present[0].at(-1)?.date;
  return `<svg class="rs-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label}"><line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line><line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}"></line>${paths}</svg><div class="chart-legend rs-legend">${legend}<span>${first} → ${last}</span></div>`;
}

function setAnalysisOptions() {
  const choices = [...data.assets].sort((a, b) => b.rs_score - a.rs_score);
  for (const id of ['rsTrendTicker', 'compareA', 'compareB']) {
    const select = $(id);
    if (select.options.length) continue;
    choices.forEach(asset => select.add(new Option(`${asset.ticker} · ${asset.sector_etf}`, asset.ticker)));
  }
  if (choices.length) {
    $('rsTrendTicker').value = selectedTicker || choices[0].ticker;
    $('compareA').value = choices[0].ticker;
    $('compareB').value = choices[Math.min(1, choices.length - 1)].ticker;
  }
}

function renderAnalysis() {
  setAnalysisOptions();
  const selected = $('rsTrendTicker').value;
  $('rsTrendChart').innerHTML = rsChart({[selected]: rsRows(selected, 20)}, `Tendencia RS de ${selected}`);
  const first = $('compareA').value, second = $('compareB').value;
  $('compareChart').innerHTML = first && second ? rsChart({[first]: rsRows(first, 40), [second]: rsRows(second, 40)}, `Comparador RS ${first} y ${second}`) : '<div class="chart-empty">Seleccioná dos activos.</div>';
  const sessions = [...data.assets].filter(asset => asset.sessions_volume_score != null).sort((a, b) => b.sessions_volume_score - a.sessions_volume_score).slice(0, 15);
  $('sessionsRows').innerHTML = sessions.map(asset => row([`<button class="ticker" data-analysis-ticker="${asset.ticker}">${asset.ticker}</button>`, asset.sector_etf, asset.up_sessions_10, `${f(asset.volume_ratio10_50)}x`, f(asset.sessions_volume_score)])).join('') || row(['DATA UNAVAILABLE', '', '', '', '']);
  const volume = [...data.assets].filter(asset => asset.unusual_volume).sort((a, b) => b.volume_deviation_pct - a.volume_deviation_pct).slice(0, 15);
  $('volumeRows').innerHTML = volume.map(asset => { const direction = asset.volume_direction || 'NEUTRAL'; return row([`<button class="ticker" data-analysis-ticker="${asset.ticker}">${asset.ticker}</button>`, asset.sector_etf, p(asset.volume_deviation_pct), p(asset.daily_return_pct), `<span class="volume-${direction.toLowerCase()}">${direction}</span>`]); }).join('') || row(['Sin volumen inusual al último cierre.', '', '', '', '']);
  document.querySelectorAll('[data-analysis-ticker]').forEach(button => button.onclick = () => { detail(data.assets.find(asset => asset.ticker === button.dataset.analysisTicker)); activateTab('opportunities'); });
}

function drawAssets() {
  const query = $('search').value.toUpperCase();
  const state = $('state').value, sector = $('sector').value, minimum = Number($('minScore').value || 0), minimumRs = Number($('minRsScore').value || 0);
  const universe = data.assets.slice(10);
  const baseRows = universe.filter(asset => asset.ticker.includes(query) && (!state || asset.state === state) && (!sector || asset.sector_etf === sector) && asset.opportunity_score >= minimum && (asset.rs_score ?? -1) >= minimumRs && (!$('hideExtended').checked || !asset.alerts.includes('EXTENDED / DO NOT CHASE')) && (!$('hideWeakening').checked || !asset.alerts.includes('WEAKENING')) && (!$('onlyFresh').checked || asset.fresh_setup) && (!$('onlyVolume').checked || asset.unusual_volume) && (!$('onlyNewEntries').checked || asset.eligible_for_new_entry) && (!$('onlySelectiveSetup').checked || asset.selective_setup_candidate));
  const rows = baseRows.filter(matchesKpi);
  $('assetCount').textContent = `${rows.length} activos fuera del Top 10`;
  renderQuickKpis(data.assets);
  renderSpotlight(data.assets.slice(0, 3));
  $('assetRows').innerHTML = rows.map(asset => row([`<button class="ticker" data-ticker="${asset.ticker}">${asset.ticker}</button>`, asset.sector_etf, operationalBadge(asset), `<b>${f(asset.opportunity_score, 0)}</b>${asset.presentation_penalty ? ` <small class="penalty">−${asset.presentation_penalty}</small>` : ''}`, f(asset.score, 0), `<span class="state ${asset.state.replace(' ', '_')}">${asset.state}</span>`, f(asset.rs_score, 1), p(asset.rs_change_5d_pct), asset.technical_stage || '—', asset.setup_event ? `<span class="${asset.fresh_setup ? 'fresh-setup' : ''}">${asset.setup_event}${asset.setup_age_sessions != null ? ` · ${asset.setup_age_sessions}d` : ''}</span>` : '—', `<span class="alerts">${alerts(asset) || '—'}</span>`])).join('') || row(['No hay activos con los filtros actuales.', '', '', '', '', '', '', '', '', '', '']);
  document.querySelectorAll('.ticker').forEach(button => button.onclick = () => detail(data.assets.find(asset => asset.ticker === button.dataset.ticker)));
  const selected = rows.find(asset => asset.ticker === selectedTicker) || rows[0];
  if (selected) detail(selected);
}

function applyPreset(name) {
  const fields = {
    search: '', state: '', sector: '', minScore: '65', minRsScore: '0',
    hideExtended: true, hideWeakening: true, onlyFresh: false, onlyVolume: false, onlyNewEntries: false, onlySelectiveSetup: false,
  };
  if (name === 'fresh') Object.assign(fields, {minRsScore: '80', onlyFresh: true});
  if (name === 'rs') Object.assign(fields, {minRsScore: '80'});
  if (name === 'operable') Object.assign(fields, {onlyNewEntries: true});
  if (name === 'selective') Object.assign(fields, {onlySelectiveSetup: true});
  Object.entries(fields).forEach(([id, value]) => {
    const element = $(id);
    if (element.type === 'checkbox') element.checked = value;
    else element.value = value;
  });
  document.querySelectorAll('.preset').forEach(button => button.classList.toggle('active', button.dataset.preset === (name === 'reset' ? 'base' : name)));
  drawAssets();
}

function currentRotationDate() { return rotationDates[Number($('rotationTimeline').value || 0)]; }

function drawRotation() {
  const date = currentRotationDate();
  const assetMode = $('rotationMode').value === 'assets';
  const source = assetMode ? data.asset_rotation_history : data.rotation_history;
  const rows = source.filter(item => item.date === date).sort((a, b) => (b.rs_score ?? b.relative_strength20_pct ?? 0) - (a.rs_score ?? a.relative_strength20_pct ?? 0));
  const map = $('rotationMap');
  map.querySelectorAll('.bubble').forEach(item => item.remove());
  $('rotationCurrent').textContent = date || 'DATA UNAVAILABLE';
  $('rotationSymbolHead').textContent = assetMode ? 'Ticker' : 'ETF';
  $('rotationSectorHead').textContent = assetMode ? 'ETF' : '—';
  $('rotationBreadthHead').textContent = assetMode ? '—' : 'Breadth SMA50';
  const leading = rows.filter(item => item.state === 'LEADING').length;
  const unusual = rows.filter(item => Number(item.acceleration_pct || 0) > 0).sort((a, b) => Number(b.acceleration_pct || 0) - Number(a.acceleration_pct || 0)).slice(0, 3).map(item => item.ticker || item.etf).join(', ');
  $('rotationInsight').textContent = rows.length ? `${leading} en LEADING. Mayor aceleración: ${unusual || '—'}. ${assetMode && rows.length > 70 ? 'El mapa muestra una selección equilibrada de 70 acciones; la tabla conserva el universo disponible.' : ''}` : 'DATA UNAVAILABLE';
  const mapRows = assetMode && rows.length > 70
    ? [...rows.slice(0, 45), ...rows.slice(-15), ...[...rows].sort((a, b) => Number(b.acceleration_pct || 0) - Number(a.acceleration_pct || 0)).slice(0, 10)].filter((row, index, list) => list.findIndex(item => (item.ticker || item.etf) === (row.ticker || row.etf)) === index)
    : rows;
  mapRows.forEach(item => {
    const relative = Number(item.rs_score ?? item.relative_strength20_pct ?? 0), acceleration = Number(item.acceleration_pct || 0), breadth = Number(item.breadth_sma50_pct || 50);
    const bubble = document.createElement('span');
    bubble.className = `bubble ${item.state}`;
    bubble.style.left = `${Math.max(6, Math.min(94, 50 + (assetMode ? relative - 50 : relative * 5)))}%`;
    bubble.style.top = `${Math.max(6, Math.min(94, 50 - acceleration * 9))}%`;
    const size = Math.max(30, Math.min(72, 26 + breadth * .46));
    bubble.style.width = bubble.style.height = `${size}px`;
    const symbol = item.ticker || item.etf;
    bubble.title = `${symbol}: ${item.state} · RS ${assetMode ? f(relative, 1) : p(Number(item.relative_strength20_pct))}, aceleración ${p(acceleration)}`;
    bubble.textContent = symbol;
    map.append(bubble);
  });
  $('sectorRows').innerHTML = rows.map(item => {
    return assetMode
      ? row([item.ticker, item.sector_etf, f(item.rs_score, 1), p(item.relative_strength20_pct), '—', p(item.acceleration_pct), '—', stateBadge(item.state)])
      : row([item.etf, '—', '—', p(item.relative_strength20_pct), p(item.momentum20_pct), p(item.acceleration_pct), p(item.breadth_sma50_pct), stateBadge(item.state)]);
  }).join('');
}

function setOptions() {
  for (const sector of [...new Set(data.assets.map(asset => asset.sector_etf))].sort()) $('sector').add(new Option(sector, sector));
  rotationDates = [...new Set(data.rotation_history.map(item => item.date))].sort();
  const timeline = $('rotationTimeline');
  timeline.min = 0; timeline.max = Math.max(0, rotationDates.length - 1); timeline.value = timeline.max;
  $('rotationStart').textContent = rotationDates[0] || '—';
  $('rotationEnd').textContent = rotationDates.at(-1) || '—';
}

const fundamentalMetrics = [
  ['trailing_pe', 'P/E', true, value => `${f(value)}x`],
  ['peg_forward', 'PEG esperado', true, value => `${f(value)}x`],
  ['forward_pe', 'Forward P/E', true, value => `${f(value)}x`],
  ['ev_sales', 'EV/Sales', true, value => `${f(value)}x`],
  ['ev_ebitda', 'EV/EBITDA', true, value => `${f(value)}x`],
  ['price_to_sales', 'P/S', true, value => `${f(value)}x`],
  ['price_to_book', 'P/B', true, value => `${f(value)}x`],
  ['roe_pct', 'ROE', false, p],
  ['operating_margin_pct', 'Margen operativo', false, p],
  ['eps_growth_next_5y_pct', 'EPS growth 5y', false, p],
  ['revenue_growth_past_5y_pct', 'Sales growth 5y', false, p],
  ['fcf_yield_pct', 'FCF yield', false, p],
];

function fundamentalCell(asset, key, lowerBetter, formatValue) {
  const value = asset.fundamentals?.metrics?.[key];
  if (value == null || !Number.isFinite(Number(value))) return '<span class="fund-missing" title="Finviz no informó este dato">—</span>';
  const peers = asset.fundamental_peers || {};
  const average = peers.averages?.[key];
  const count = peers.counts?.[key] || 0;
  if (average == null || !count) return `<span class="fund-value">${formatValue(value)}</span><small class="peer-context">Promedio pares: —</small>`;
  const delta = Number(value) - Number(average);
  const relative = Number(average) === 0 ? null : delta / Math.abs(Number(average)) * 100;
  const neutral = Math.abs(delta) < Math.max(0.01, Math.abs(Number(average)) * 0.05);
  const favorable = lowerBetter ? delta < 0 : delta > 0;
  const state = neutral ? 'peer-neutral' : favorable ? 'peer-better' : 'peer-worse';
  const sign = delta > 0 ? '+' : '';
  const deviation = relative == null ? `${sign}${f(delta)} vs promedio` : `${sign}${f(relative, 0)}% vs promedio`;
  return `<span class="fund-value ${state}">${formatValue(value)}</span><small class="peer-context">Pares ${count}: ${formatValue(average)} · ${deviation}</small>`;
}

function renderFundamentalSectorMap(assets) {
  const sectors = new Map();
  for (const asset of assets) {
    const sector = asset.fundamentals?.sector;
    if (!sector) continue;
    if (!sectors.has(sector)) sectors.set(sector, []);
    sectors.get(sector).push(asset);
  }
  $('fundamentalSectorMap').innerHTML = [...sectors.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([sector, rows]) => {
    const average = key => {
      const values = rows.map(asset => Number(asset.fundamentals?.metrics?.[key])).filter(Number.isFinite);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    };
    const active = $('fundSector').value === sector ? ' selected' : '';
    return `<button type="button" class="fund-sector-card${active}" data-fund-sector="${escapeHtml(sector)}"><span>${escapeHtml(sector)}</span><b>${rows.length} empresas</b><small>PEG ${average('peg_forward') == null ? '—' : `${f(average('peg_forward'))}x`} · ROE ${p(average('roe_pct'))} · EV/Sales ${average('ev_sales') == null ? '—' : `${f(average('ev_sales'))}x`}</small></button>`;
  }).join('') || '<div class="empty">DATA UNAVAILABLE · Importá un CSV exportado desde Finviz Elite para clasificar sectores. Esta exportación requiere acceso Elite.</div>';
  document.querySelectorAll('[data-fund-sector]').forEach(button => button.onclick = () => {
    $('fundSector').value = $('fundSector').value === button.dataset.fundSector ? '' : button.dataset.fundSector;
    renderFundamentals();
  });
}

function renderFundamentals() {
  const all = data.assets.filter(asset => ['OK', 'STALE'].includes(asset.fundamentals?.status)
    && asset.fundamentals?.source === 'Finviz CSV export'
    && asset.fundamentals?.schema_version === 3);
  const sectors = [...new Set(all.map(asset => asset.fundamentals.sector).filter(Boolean))].sort();
  const current = $('fundSector').value;
  $('fundSector').innerHTML = '<option value="">Todos los sectores</option>' + sectors.map(sector => `<option value="${escapeHtml(sector)}">${escapeHtml(sector)}</option>`).join('');
  if (sectors.includes(current)) $('fundSector').value = current;
  const industries = new Set(all.map(asset => asset.fundamentals.industry).filter(Boolean));
  const fundamentalState = data.fundamentals || {};
  const hasFinvizSnapshot = fundamentalState.source === 'Finviz CSV export'
    && ['OK', 'STALE'].includes(fundamentalState.status)
    && all.length > 0;
  const asOf = hasFinvizSnapshot && fundamentalState.as_of ? String(fundamentalState.as_of).slice(0, 10) : null;
  $('fundamentalsAsOf').textContent = `Datos al ${data.as_of || '—'}`;
  $('fundamentalCoverage').textContent = `${all.length} / ${data.assets.length} empresas con métricas`;
  $('fundamentalSourceStamp').textContent = hasFinvizSnapshot
    ? `Fuente: Finviz Elite CSV · datos al ${asOf} · snapshot de mercado ${data.as_of || '—'}${fundamentalState.status === 'STALE' ? ` · EXPORTACIÓN DESACTUALIZADA (${fundamentalState.stale_count} filas)` : ''} · ${industries.size} industrias identificadas.`
    : `Fuente: DATA UNAVAILABLE · falta una exportación Finviz Elite válida · snapshot de mercado ${data.as_of || '—'}.`;
  $('fundamentalKpis').innerHTML = [
    ['Empresas cubiertas', `${all.length} / ${data.assets.length}`],
    ['Sectores', sectors.length],
    ['Industrias / grupos de pares', industries.size],
    ['Con PEG proyectado', all.filter(asset => asset.fundamentals.metrics?.peg_forward != null).length],
  ].map(([label, value]) => `<div class="metric">${label}<b>${value}</b></div>`).join('');
  renderFundamentalSectorMap(all);
  const query = $('fundSearch').value.trim().toUpperCase();
  const sectorFilter = $('fundSector').value;
  const sort = $('fundSort').value;
  const filtered = all.filter(asset => (!sectorFilter || asset.fundamentals.sector === sectorFilter)
    && (!query || asset.ticker.includes(query) || (asset.fundamentals.industry || '').toUpperCase().includes(query)))
    .sort((a, b) => {
      if (sort === 'ticker') return a.ticker.localeCompare(b.ticker);
      const av = Number(a.fundamentals.metrics?.[sort]), bv = Number(b.fundamentals.metrics?.[sort]);
      if (!Number.isFinite(av)) return 1;
      if (!Number.isFinite(bv)) return -1;
      return ['peg_forward', 'forward_pe', 'ev_sales', 'ev_ebitda', 'price_to_book', 'price_to_sales', 'trailing_pe'].includes(sort) ? av - bv : bv - av;
    });
  $('fundamentalCount').textContent = `${filtered.length} empresas visibles · comparación informativa`;
  $('fundamentalRows').innerHTML = filtered.map(asset => {
    const industry = asset.fundamentals.industry || 'Industria no informada';
    const peerGroup = asset.fundamental_peers?.peer_group || industry;
    const metrics = Object.fromEntries(fundamentalMetrics.map(([key, label, lower, format]) => [key, fundamentalCell(asset, key, lower, format)]));
    return row([
      `<a class="ticker" href="${asset.fundamentals.source_url || `https://finviz.com/quote.ashx?t=${encodeURIComponent(asset.ticker)}`}" target="_blank" rel="noopener">${escapeHtml(asset.ticker)}</a>`,
      `<span>${escapeHtml(industry)}</span><small class="peer-context">Pares: ${escapeHtml(peerGroup)}</small>`,
      metrics.peg_forward, metrics.forward_pe, metrics.ev_sales, metrics.ev_ebitda, metrics.price_to_sales, metrics.price_to_book,
      metrics.roe_pct, metrics.operating_margin_pct, metrics.eps_growth_next_5y_pct, metrics.revenue_growth_past_5y_pct, metrics.fcf_yield_pct,
    ]);
  }).join('') || row([all.length ? 'No hay empresas con métricas de Finviz para esos filtros.' : 'DATA UNAVAILABLE · Importá un CSV exportado desde Finviz Elite para publicar la matriz.', '', '', '', '', '', '', '', '', '', '', '']);
}

async function ensureAssetRotation() {
  if (assetRotationLoaded) return true;
  if (assetRotationLoading) return assetRotationLoading;
  assetRotationLoading = fetch('data/asset_rotation.json', {cache: 'no-store'})
    .then(response => { if (!response.ok) throw Error('Mapa de acciones no disponible'); return response.json(); })
    .then(payload => {
      if (payload.status !== 'OK' || !Array.isArray(payload.rows)) throw Error('Datos de rotación de acciones no disponibles');
      data.asset_rotation_history = payload.rows;
      assetRotationLoaded = true;
      return true;
    })
    .catch(() => false)
    .finally(() => { assetRotationLoading = null; });
  return assetRotationLoading;
}

function togglePlayback() {
  if (playback) { clearInterval(playback); playback = null; $('rotationPlay').textContent = 'Reproducir'; return; }
  $('rotationPlay').textContent = 'Pausar';
  playback = setInterval(() => {
    const timeline = $('rotationTimeline');
    timeline.value = Number(timeline.value) >= Number(timeline.max) ? 0 : Number(timeline.value) + 1;
    drawRotation();
  }, 260);
}

async function init() {
  let snapshotLoaded = false;
  try {
    const response = await fetch('data/dashboard.json', {cache: 'no-store'});
    if (!response.ok) throw Error();
    data = await response.json();
    snapshotLoaded = true;
    data.asset_rotation_history ||= [];
    assetRotationLoaded = false;
    loadedRefreshAttempt = data.refresh?.attempted_at || null;
    $('loading').hidden = true;
    $('updated').textContent = `Datos al ${data.as_of || 'DATA UNAVAILABLE'} · actualización ${data.refresh?.attempted_at ? new Date(data.refresh.attempted_at).toLocaleString() : '—'}`;
    const market = data.market;
    $('market').innerHTML = [['Régimen', market.regime], ['QQQ / SPY', `${f(market.qqq_spy_ratio, 4)} · ${p(market.qqq_vs_spy_20d_pct)}`], ['VIX', `${f(market.vix)} · ${market.vix_state}`], ['Gate de entrada', gate().status], ['Fuente', data.source]].map(item => `<div class="metric">${item[0]}<b>${item[1]}</b></div>`).join('');
    if (data.status !== 'OK' || data.refresh?.errors?.length) { $('loading').hidden = false; $('loading').innerHTML = `<b>${data.status !== 'OK' ? 'DATA UNAVAILABLE' : 'Última actualización parcial'}</b><br>${data.refresh?.errors?.[0] || 'Se conserva el último snapshot válido.'}`; }
    renderOperationalGate(); renderTop10(); renderSelectiveSetup(); renderWatchlist(); renderJournal(); setOptions(); drawAssets(); drawRotation(); renderHeatmap();
    $('trackRows').innerHTML = (data.track_record || []).map(item => row([item.ticker, item.sector_etf, item.event, item.inclusion_date, p(item.return_pct), p(item.benchmark_return_pct), p(item.excess_return_pct)])).join('');
    $('trackSectorRows').innerHTML = (data.track_record_sector_summary || []).map(item => row([item.sector_etf, item.included, item.valid_returns, `${f(item.win_rate_pct)}%`, p(item.average_return_pct), p(item.benchmark_return_pct), p(item.excess_return_pct)])).join('');
    renderFundamentals();
  } catch (error) {
    console.error('No se pudo inicializar el dashboard', error);
    $('loading').hidden = false;
    $('loading').innerHTML = snapshotLoaded
      ? `El snapshot sí se cargó, pero hubo un error al mostrar el dashboard: <code>${escapeHtml(error?.message || 'error de interfaz')}</code>.`
      : 'No hay snapshot disponible en esta instancia. Revisá la actualización de datos o abrí la instancia local correcta.';
  }
}

async function pollRefresh() {
  try {
    const status = await (await fetch('/api/refresh-status', {cache: 'no-store'})).json();
    if (status.running) {
      $('updated').textContent = `Datos al ${data.as_of || '—'} · actualizando en segundo plano…`;
      return;
    }
    if (!status.finished_at) return;
    const snapshot = await (await fetch('data/dashboard.json', {cache: 'no-store'})).json();
    if (snapshot.refresh?.attempted_at && snapshot.refresh.attempted_at !== loadedRefreshAttempt) window.location.reload();
  } catch (_) {
    // Static deployments do not expose the local endpoint; the snapshot stays usable.
  }
}

['search', 'state', 'sector', 'minScore', 'minRsScore', 'hideExtended', 'hideWeakening', 'onlyFresh', 'onlyVolume', 'onlyNewEntries', 'onlySelectiveSetup'].forEach(id => $(id).addEventListener('input', drawAssets));
document.querySelectorAll('.preset').forEach(button => button.addEventListener('click', () => applyPreset(button.dataset.preset)));
setupExecutionDialog();
$('rotationTimeline').addEventListener('input', drawRotation);
$('rotationPlay').addEventListener('click', togglePlayback);
$('rotationMode').addEventListener('change', async () => {
  if ($('rotationMode').value === 'assets') {
    $('rotationInsight').textContent = 'Cargando rotación histórica de acciones…';
    const loaded = await ensureAssetRotation();
    if (!loaded) $('rotationInsight').textContent = 'DATA UNAVAILABLE · No fue posible cargar el mapa histórico de acciones.';
  }
  drawRotation();
});
['rsTrendTicker', 'compareA', 'compareB'].forEach(id => $(id).addEventListener('change', renderAnalysis));
['fundSearch'].forEach(id => $(id).addEventListener('input', renderFundamentals));
['fundSector', 'fundSort'].forEach(id => $(id).addEventListener('change', renderFundamentals));
async function activateTab(name) {
  document.querySelectorAll('[data-view]').forEach(view => view.hidden = view.dataset.view !== name);
  document.querySelectorAll('.tabs button').forEach(item => item.classList.toggle('active', item.dataset.tab === name));
  const viewLabels = {
    opportunities: ['Top 10', 'Qué mirar hoy, en orden de prioridad.'],
    universe: ['Universo', 'Explorá el resto de activos y sus condiciones técnicas.'],
    journal: ['Operativa', 'Watchlist y seguimiento prospectivo de señales.'],
    rotation: ['Rotación', 'Fuerza relativa y evolución sectorial.'],
    heatmap: ['Heatmap', 'Variación diaria del universo.'],
    analysis: ['Análisis', 'Comparación de fuerza relativa y volumen.'],
    track: ['Track record', 'Rendimientos observados y exceso frente a SPY.'],
    fundamentals: ['Fundamentales', 'Comparación descriptiva por sector, industria y pares.'],
  };
  if (viewLabels[name]) {
    $('viewTitle').textContent = viewLabels[name][0];
    $('viewHint').textContent = viewLabels[name][1];
  }
  if (name === 'fundamentals') renderFundamentals();
  if (name === 'analysis') {
    const loaded = await ensureAssetRotation();
    if (loaded) renderAnalysis();
    else $('rsTrendChart').innerHTML = '<div class="chart-empty">DATA UNAVAILABLE · No fue posible cargar la historia RS.</div>';
  }
}
document.querySelectorAll('.tabs button').forEach(button => button.onclick = () => activateTab(button.dataset.tab));
init();
setInterval(pollRefresh, 30000);
