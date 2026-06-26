/* ════════════════════════════════════════════════
   FaceGuard Dashboard — JavaScript
   Handles: live polling, charts, toasts, retrain
   ════════════════════════════════════════════════ */

/* ── Shared state ──────────────────────────────── */
let _hourlyChart = null;
let _weeklyChart = null;
let _statsTimer  = null;
let _timelineTimer = null;
let _chartsTimer = null;
let _systemTimer = null;

/* ══════════════════════════════════════════════════
   CLOCK & SIDEBAR
══════════════════════════════════════════════════ */
function startClock() {
  const el = document.getElementById('live-clock');
  if (!el) return;
  function tick() {
    const now = new Date();
    el.textContent = now.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  }
  tick();
  setInterval(tick, 1000);
}

function initSidebar() {
  const toggle  = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (!toggle || !sidebar) return;

  toggle.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    overlay.classList.toggle('show');
  });
  overlay.addEventListener('click', () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
  });
}

/* ══════════════════════════════════════════════════
   TOAST NOTIFICATIONS
══════════════════════════════════════════════════ */
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const colours = { success:'#22C55E', danger:'#EF4444', info:'#3B82F6', warning:'#F59E0B' };
  const icons   = { success:'fa-check-circle', danger:'fa-xmark-circle', info:'fa-circle-info', warning:'fa-triangle-exclamation' };

  const id = 'toast-' + Date.now();
  const div = document.createElement('div');
  div.id = id;
  div.style.cssText = `
    background:#1E293B;border:1px solid #334155;border-radius:10px;
    padding:12px 16px;margin-top:8px;display:flex;align-items:center;gap:10px;
    box-shadow:0 8px 24px rgba(0,0,0,.4);min-width:220px;max-width:320px;
    animation:slideIn .25s ease;color:#F1F5F9;font-size:.85rem;
  `;
  div.innerHTML = `
    <i class="fas ${icons[type]||icons.info}" style="color:${colours[type]||colours.info};font-size:1rem;"></i>
    <span style="flex:1;">${message}</span>
    <button onclick="document.getElementById('${id}').remove()"
            style="background:none;border:none;color:#94A3B8;cursor:pointer;font-size:.9rem;padding:0;">
      <i class="fas fa-xmark"></i>
    </button>
  `;
  container.appendChild(div);
  setTimeout(() => div.remove(), 4500);
}

// CSS for toast animation
const toastStyle = document.createElement('style');
toastStyle.textContent = '@keyframes slideIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}';
document.head.appendChild(toastStyle);

/* ══════════════════════════════════════════════════
   LIVE STATS POLLING  (every 2 s)
══════════════════════════════════════════════════ */
function updateLockBadge(lockStatus, state) {
  const badge = document.getElementById('lock-badge');
  const text  = document.getElementById('lock-text');
  if (!badge) return;
  badge.className = 'status-badge';
  if (lockStatus === 'OPEN') {
    badge.classList.add('badge-open');
    badge.querySelector('i').className = 'fas fa-lock-open';
    if (text) text.textContent = 'OPEN';
  } else if (state === 'CONFIRMING') {
    badge.classList.add('badge-confirm');
    badge.querySelector('i').className = 'fas fa-spinner fa-spin';
    if (text) text.textContent = 'VERIFYING';
  } else {
    badge.classList.add('badge-locked');
    badge.querySelector('i').className = 'fas fa-lock';
    if (text) text.textContent = 'LOCKED';
  }
}

function pollStats() {
  fetch('/api/stats')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;

      // Lock badge (topbar)
      updateLockBadge(d.lock_status, d.system_state);

      // Stat cards
      _setText('s-users',    d.authorized_users ?? '—');
      _setText('s-unlocks',  d.unlock_count ?? '—');
      _setText('s-unknown',  d.unknown_count ?? '—');
      _setText('s-fps',      d.fps != null ? d.fps.toFixed(1) : '—');

      // Weekly / Monthly (if elements exist)
      _setText('s-w-unlocks', d.unlock_count_weekly ?? d.unlock_count ?? '—');
      _setText('s-m-unlocks', d.unlock_count_monthly ?? d.unlock_count ?? '—');

      // Live info panel
      const person = d.confirmed_person || null;
      const conf   = d.confirmed_conf   || 0;

      _setText('live-person', person || 'No Face Detected');
      _setText('live-conf',   person ? `${conf.toFixed(1)}% confidence` : '');
      _setText('live-lock',   d.lock_status);
      _setText('live-state',  d.system_state);

      // Lock value colour
      const lockEl = document.getElementById('live-lock');
      if (lockEl) lockEl.style.color = d.lock_status === 'OPEN' ? 'var(--color-success)' : 'var(--color-muted)';

      // Recognition timer (CONFIRMING state)
      const timerWrap = document.getElementById('timer-wrap');
      const timerIdle = document.getElementById('timer-idle');
      const timerBar  = document.getElementById('timer-bar');
      const timerText = document.getElementById('timer-text');
      if (timerWrap && timerIdle) {
        if (d.system_state === 'CONFIRMING' && d.confirm_progress != null) {
          timerWrap.style.display = 'block';
          timerIdle.style.display = 'none';
          if (timerBar)  timerBar.style.width = (d.confirm_progress * 100).toFixed(1) + '%';
          if (timerText) timerText.textContent = `Verifying ${person}…`;
        } else {
          timerWrap.style.display = 'none';
          timerIdle.style.display = 'block';
        }
      }

      // Camera page live info
      _setText('cam-person', person || 'No Face Detected');
      _setText('cam-conf',   person ? `${conf.toFixed(1)}%` : '—');
      _setText('cam-state',  d.system_state);
      _setText('cam-lock',   d.lock_status);
      _setText('cam-fps',    d.fps != null ? `${d.fps.toFixed(1)} FPS` : '—');
      _setText('stream-fps', d.fps != null ? `${d.fps.toFixed(1)} FPS` : '');

      const camTimerWrap = document.getElementById('cam-timer-wrap');
      const camTimerBar  = document.getElementById('cam-timer-bar');
      const camTimerText = document.getElementById('cam-timer-text');
      if (camTimerWrap) {
        if (d.system_state === 'CONFIRMING') {
          camTimerWrap.style.display = 'block';
          if (camTimerBar)  camTimerBar.style.width = (d.confirm_progress * 100).toFixed(1) + '%';
          if (camTimerText) camTimerText.textContent = `Verifying ${person}…`;
        } else {
          camTimerWrap.style.display = 'none';
        }
      }
    })
    .catch(() => {});
}

/* ══════════════════════════════════════════════════
   TIMELINE POLLING  (every 3 s)
══════════════════════════════════════════════════ */
function pollTimeline() {
  fetch('/api/timeline')
    .then(r => r.ok ? r.json() : null)
    .then(events => {
      if (!events) return;
      const list = document.getElementById('timeline-list');
      if (!list) return;

      if (!events.length) {
        list.innerHTML = '<div class="text-center text-muted py-4 small">No events yet</div>';
        return;
      }

      list.innerHTML = events.map(e => {
        const typeMap = {
          granted: { cls: 'granted', icon: 'fa-door-open',    label: 'Door Opened' },
          denied:  { cls: 'denied',  icon: 'fa-xmark',        label: 'Access Denied' },
          unknown: { cls: 'unknown', icon: 'fa-user-secret',  label: 'Unknown Detected' },
        };
        const t = typeMap[e.type] || typeMap.unknown;
        const ts = e.timestamp ? e.timestamp.substring(11, 16) : '';

        return `
          <div class="timeline-item">
            <div class="tl-dot ${t.cls}"><i class="fas ${t.icon}"></i></div>
            <div>
              <div class="tl-time">${e.timestamp || ''}</div>
              <div class="tl-msg">${e.message || t.label}</div>
              <div class="tl-person">${e.person || ''} ${e.confidence ? '· ' + e.confidence.toFixed(1) + '%' : ''}</div>
            </div>
          </div>`;
      }).join('');
    })
    .catch(() => {});
}

/* ══════════════════════════════════════════════════
   CHART.JS  (refreshed every 30 s)
══════════════════════════════════════════════════ */
const CHART_DEFAULTS = {
  responsive: true,
  animation: { duration: 600 },
  plugins: { legend: { labels: { color: '#94A3B8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#94A3B8', font: { size: 10 } }, grid: { color: '#273549' } },
    y: { ticks: { color: '#94A3B8', font: { size: 10 } }, grid: { color: '#273549' }, beginAtZero: true, precision: 0 },
  },
};

function initCharts() {
  const hourlyCtx = document.getElementById('hourly-chart');
  const weeklyCtx = document.getElementById('weekly-chart');

  if (hourlyCtx) {
    _hourlyChart = new Chart(hourlyCtx, {
      type: 'bar',
      data: { labels: [], datasets: [
        { label: 'Unlocks',  data: [], backgroundColor: 'rgba(59,130,246,.55)',  borderColor: '#3B82F6', borderWidth: 1 },
        { label: 'Unknown',  data: [], backgroundColor: 'rgba(239,68,68,.45)',   borderColor: '#EF4444', borderWidth: 1 },
      ]},
      options: { ...CHART_DEFAULTS, plugins: { ...CHART_DEFAULTS.plugins,
        tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.raw}` } }
      }},
    });
  }

  if (weeklyCtx) {
    _weeklyChart = new Chart(weeklyCtx, {
      type: 'line',
      data: { labels: [], datasets: [
        { label: 'Unlocks', data: [], borderColor: '#3B82F6', backgroundColor: 'rgba(59,130,246,.12)',
          fill: true, tension: .4, pointRadius: 4, pointBackgroundColor: '#3B82F6' },
        { label: 'Unknown', data: [], borderColor: '#EF4444', backgroundColor: 'rgba(239,68,68,.10)',
          fill: true, tension: .4, pointRadius: 4, pointBackgroundColor: '#EF4444' },
      ]},
      options: CHART_DEFAULTS,
    });
  }

  refreshCharts();
}

function refreshCharts() {
  if (_hourlyChart) {
    fetch('/api/chart/hourly').then(r => r.json()).then(d => {
      _hourlyChart.data.labels           = d.labels;
      _hourlyChart.data.datasets[0].data = d.unlocks;
      _hourlyChart.data.datasets[1].data = d.unknowns;
      _hourlyChart.update('none');
    }).catch(() => {});
  }
  if (_weeklyChart) {
    fetch('/api/chart/weekly').then(r => r.json()).then(d => {
      _weeklyChart.data.labels           = d.labels;
      _weeklyChart.data.datasets[0].data = d.unlocks;
      _weeklyChart.data.datasets[1].data = d.unknowns;
      _weeklyChart.update('none');
    }).catch(() => {});
  }
}

/* ══════════════════════════════════════════════════
   SYSTEM PAGE POLLING  (every 2 s)
══════════════════════════════════════════════════ */
function pollSystem() {
  fetch('/api/system')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d) return;

      // Progress bars + values
      _setBar('bar-cpu',  d.cpu_percent); _setText('sys-cpu',  d.cpu_percent  != null ? d.cpu_percent.toFixed(1)  + '%' : '—');
      _setBar('bar-ram',  d.ram_percent); _setText('sys-ram',  d.ram_percent  != null ? d.ram_percent.toFixed(1)  + '%' : '—');
      _setBar('bar-disk', d.disk_percent);_setText('sys-disk', d.disk_percent != null ? d.disk_percent.toFixed(1) + '%' : '—');

      // Temperature with colour coding
      if (d.cpu_temp != null) {
        _setText('sys-temp', d.cpu_temp + '°C');
        const icon  = document.getElementById('temp-icon');
        const label = document.getElementById('temp-label');
        let col = 'var(--color-success)', lbl = 'Normal';
        if (d.cpu_temp >= 75) { col = 'var(--color-danger)';  lbl = 'Critical!'; }
        else if (d.cpu_temp >= 60) { col = 'var(--color-warning)'; lbl = 'Warm'; }
        if (icon)  icon.style.color = col;
        if (label) label.textContent = lbl;
      }

      // Status indicators
      _setStatus('st-engine', d.engine_running ? 'RUNNING' : 'STOPPED', d.engine_running ? 'online' : 'offline');
      _setStatus('st-servo',  d.servo_state,
        d.servo_state === 'OPEN' ? 'online' : (d.servo_state === 'CLOSED' ? 'warning' : 'offline'));
      _setText('st-fps', d.fps != null ? `${d.fps} FPS` : '—');
      const fpsEl = document.getElementById('st-fps');
      if (fpsEl) { fpsEl.className = 'status-dot ' + (d.fps > 0 ? 'online' : 'offline'); }

      // Info table
      _setText('inf-ip',     d.ip     || '—');
      _setText('inf-uptime', d.uptime || '—');
      _setText('inf-python', d.python || '—');
      _setText('inf-db',     d.db_size_kb != null ? d.db_size_kb + ' KB' : '—');
      _setText('inf-ram',    (d.ram_used_mb && d.ram_total_mb) ? `${d.ram_used_mb} / ${d.ram_total_mb} MB` : '—');
      _setText('inf-disk',   (d.disk_used_gb && d.disk_total_gb) ? `${d.disk_used_gb} / ${d.disk_total_gb} GB` : '—');
    })
    .catch(() => {});
}

/* ══════════════════════════════════════════════════
   CAMERA — stream refresh
══════════════════════════════════════════════════ */
function refreshStream() {
  const img = document.getElementById('stream');
  if (img) {
    img.src = '/video_feed?' + Date.now();
    showToast('Stream refreshed.', 'info');
  }
}

/* ══════════════════════════════════════════════════
   RETRAIN
══════════════════════════════════════════════════ */
function triggerRetrain() {
  const statusDiv = document.getElementById('retrain-status');
  const msgSpan   = document.getElementById('retrain-msg');

  fetch('/api/retrain', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { showToast(d.message || 'Already running.', 'warning'); return; }
      showToast('Retraining started…', 'info');
      if (statusDiv) statusDiv.style.display = 'block';

      const poll = setInterval(() => {
        fetch('/api/retrain_status').then(r => r.json()).then(s => {
          if (msgSpan) msgSpan.textContent = s.message || 'Working…';
          if (!s.running) {
            clearInterval(poll);
            if (statusDiv) statusDiv.style.display = 'none';
            if (s.success) showToast('Database retrained successfully!', 'success');
            else           showToast('Retrain failed: ' + s.message, 'danger');

            // Disable retrain buttons while running, re-enable
            document.querySelectorAll('[id^="btn-retrain"]').forEach(b => b.disabled = false);
          }
        });
      }, 2000);

      document.querySelectorAll('[id^="btn-retrain"]').forEach(b => {
        b.disabled = true;
        b.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i> Retraining…';
      });
    })
    .catch(() => showToast('Failed to start retrain.', 'danger'));
}

/* ══════════════════════════════════════════════════
   PAGE INIT ENTRY POINTS
══════════════════════════════════════════════════ */

/** Call from dashboard.html */
function initDashboard() {
  startClock();
  initSidebar();
  initCharts();

  pollStats();
  pollTimeline();
  _statsTimer    = setInterval(pollStats,     2000);
  _timelineTimer = setInterval(pollTimeline,  3000);
  _chartsTimer   = setInterval(refreshCharts, 30000);
}

/** Call from camera.html */
function initCamera() {
  startClock();
  initSidebar();
  pollStats();
  _statsTimer = setInterval(pollStats, 2000);

  // Poll retrain status if already running
  fetch('/api/retrain_status').then(r => r.json()).then(s => {
    if (s.running) { triggerRetrain(); }
  }).catch(() => {});
}

/** Call from system.html */
function initSystem() {
  startClock();
  initSidebar();
  pollSystem();
  _systemTimer = setInterval(pollSystem, 2000);
}

/* ── Auto-init sidebar + clock on all other pages ── */
document.addEventListener('DOMContentLoaded', () => {
  // Always run sidebar + clock (even on pages that don't call an initXxx)
  if (!window._sidebarInited) {
    startClock();
    initSidebar();
    window._sidebarInited = true;
  }
  // Keep lock badge in sync on non-dashboard pages
  if (!_statsTimer) {
    pollStats();
    _statsTimer = setInterval(pollStats, 3000);
  }
});

/* ══════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════ */
function _setText(id, text) {
  const el = document.getElementById(id);
  if (el && text !== undefined && text !== null) el.textContent = text;
}

function _setBar(id, pct) {
  const el = document.getElementById(id);
  if (el && pct != null) el.style.width = Math.min(pct, 100).toFixed(1) + '%';
}

function _setStatus(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className   = 'status-dot ' + (cls || '');
}
