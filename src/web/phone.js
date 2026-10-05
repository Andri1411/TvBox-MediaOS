// Phone remote. Buttons send the same logical buttons a controller does, so
// the bindings (short/long press, repeat) apply unchanged.
'use strict';

const POINTER_SCALE = 1.6;      // phone px -> screen px
const LONG_TAP_MS = 450;
let healthTimer = null;

// ---- remote ---------------------------------------------------------------
function holdButton(el) {
  const button = el.dataset.button;
  const down = (event) => {
    event.preventDefault();
    el.classList.add('pressed');
    navigator.vibrate?.(8);
    send('button', { button, state: 'down' });
  };
  const up = () => {
    if (!el.classList.contains('pressed')) return;
    el.classList.remove('pressed');
    send('button', { button, state: 'up' });
  };
  el.addEventListener('pointerdown', down);
  for (const type of ['pointerup', 'pointercancel', 'pointerleave']) el.addEventListener(type, up);
  el.addEventListener('contextmenu', (event) => event.preventDefault());
}
document.querySelectorAll('[data-button]').forEach(holdButton);
document.querySelectorAll('[data-action]').forEach((el) =>
  el.addEventListener('click', () => send('action', { action: el.dataset.action })));
document.querySelectorAll('[data-key]').forEach((el) =>
  el.addEventListener('click', () => send('key', { key: el.dataset.key })));
$('type-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('type-text').value;
  if (text) send('type', { text });
  $('type-text').value = '';
});

// Touchpad: one finger moves, a tap clicks, a long tap right-clicks, two
// fingers scroll. Movement is batched per animation frame.
(() => {
  const pad = $('touchpad');
  const touches = new Map();
  let start = 0, moved = 0, pending = [0, 0], scrollRest = 0, frame = 0;
  const flush = () => {
    frame = 0;
    const [dx, dy] = pending.map((v) => Math.round(v * POINTER_SCALE));
    pending = [0, 0];
    if (dx || dy) send('pointer', { dx, dy });
  };
  pad.addEventListener('pointerdown', (event) => {
    pad.setPointerCapture(event.pointerId);
    touches.set(event.pointerId, [event.clientX, event.clientY]);
    if (touches.size === 1) { start = performance.now(); moved = 0; }
  });
  pad.addEventListener('pointermove', (event) => {
    const last = touches.get(event.pointerId);
    if (!last) return;
    const dx = event.clientX - last[0], dy = event.clientY - last[1];
    touches.set(event.pointerId, [event.clientX, event.clientY]);
    moved += Math.abs(dx) + Math.abs(dy);
    if (touches.size >= 2) {
      scrollRest += dy / 2;            // both fingers report; halve
      const steps = Math.trunc(scrollRest / 18);
      if (steps) { send('scroll', { dy: -steps }); scrollRest -= steps * 18; }
    } else {
      pending[0] += dx; pending[1] += dy;
      frame ||= requestAnimationFrame(flush);
    }
  });
  const end = (event) => {
    if (!touches.delete(event.pointerId) || touches.size) return;
    if (moved < 8) {
      const long = performance.now() - start > LONG_TAP_MS;
      navigator.vibrate?.(long ? 25 : 8);
      send('click', { button: long ? 'right' : 'left' });
    }
  };
  pad.addEventListener('pointerup', end);
  pad.addEventListener('pointercancel', (event) => touches.delete(event.pointerId));
})();

// ---- apps and settings ------------------------------------------------------
function renderApps() {
  $('apps').innerHTML = [{ id: null, name: 'Home', state: 'running', home: true }, ...(state.services ?? [])]
    .map((s) => `<li class="tap" data-id="${escapeHtml(s.id ?? '')}">
      <span>${escapeHtml(s.name)}${s.focused || (s.home && state.app === 'home') ? ' <span class="sub">· on screen</span>' : ''}</span>
      <span class="dot ${s.state === 'running' ? 'on' : s.state === 'starting' ? 'warn' : ''}"></span></li>`).join('');
}
$('apps').addEventListener('click', (event) => {
  const li = event.target.closest('li');
  if (!li) return;
  if (li.dataset.id) send('launch', { id: li.dataset.id }); else send('home');
});

function renderSettings() {
  if (document.activeElement !== $('volume')) $('volume').value = state.volume ?? 0;
  $('volume').disabled = state.volume == null;
  $('outputs').innerHTML = (state.sinks ?? []).map((s) => `<li class="tap" data-id="${s.id}">
      <span>${escapeHtml(s.name)}</span><span class="dot ${s.default ? 'on' : ''}"></span></li>`).join('')
    || '<li><span class="sub">No audio output</span></li>';
  $('scales').innerHTML = ['auto', '1', '1.25', '1.5', '2'].map((scale) =>
    `<button data-scale="${scale}" class="${scale === state.display_scale ? 'primary' : ''}">${scale === 'auto' ? 'Auto' : `${scale}×`}</button>`).join('');
  $('devices').innerHTML = (state.devices ?? []).map((d) => `<li>
      <span>${escapeHtml(d.name)} <span class="sub">· paired ${new Date(d.created * 1000).toLocaleDateString()}</span></span>
      <button data-revoke="${escapeHtml(d.id)}">Remove</button></li>`).join('');
}
function renderUpdates() {
  const u = state.update ?? {};
  const text = { checking: 'Checking…', applying: 'Installing…', none: 'Everything is up to date.',
    available: `${u.updates?.length} updates${u.download_size ? `, ${(u.download_size / 1e6).toFixed(0)} MB` : ''}.${u.reboot_for?.length ? ' A restart is needed afterwards.' : ''}`,
    done: u.reboot_for?.length ? 'Installed. Restart to finish.' : 'Installed.',
    error: `Failed: ${u.error}` }[u.status] ?? 'Updates are never installed automatically.';
  $('update-status').textContent = text;
  $('update-list').innerHTML = u.status === 'available' ? u.updates.map((p) =>
    `<li><span>${escapeHtml(p.name)}</span><span class="sub">${escapeHtml(p.old)} → ${escapeHtml(p.new)}</span></li>`).join('') : '';
  $('update-check').disabled = ['checking', 'applying'].includes(u.status);
  $('update-apply').hidden = u.status !== 'available';
  $('update-reboot').hidden = !(u.status === 'done' && u.reboot_for?.length);
  $('update-log').textContent = ['checking', 'applying'].includes(u.status) ? (u.log ?? []).join('\n') : '';
  $('snapshots').innerHTML = (u.snapshots ?? []).filter((s) => s.type !== 'post').map((s) => `<li>
      <span>${escapeHtml(new Date(`${s.date.replace(' ', 'T')}Z`).toLocaleString())}<br><span class="sub">${
        s.number === u.booted_snapshot ? 'running now' : s.number === u.fallback ? 'before the last update'
          : escapeHtml(s.description.startsWith('pacman') ? 'before an update' : s.description)}</span></span>
      <span class="row" style="margin:0;flex:0 0 auto"><button data-once="${s.number}">Start once</button>
      <button data-rollback="${s.number}" class="danger">Roll back</button></span></li>`).join('');
}
function renderWifi() {
  const n = state.network ?? {};
  $('wifi-status').textContent = !n.wifi_device ? (n.ethernet ? 'Connected by cable (no Wi-Fi hardware).' : 'No Wi-Fi hardware.')
    : n.connecting ? `Connecting to ${n.connecting}…` : n.message || (n.ssid ? `Connected to ${n.ssid}.` : n.ethernet ? 'Connected by cable.' : 'Not connected.');
  $('wifi-scan').disabled = !n.wifi_device || n.scanning;
  $('wifi-scan').textContent = n.scanning ? 'Searching…' : 'Search';
  $('wifi-list').innerHTML = (n.networks ?? []).map((net) => {
    const saved = (n.known ?? []).includes(net.ssid);
    return `<li class="tap" data-ssid="${escapeHtml(net.ssid)}" data-secure="${net.secure}" data-saved="${saved}">
      <span>${escapeHtml(net.ssid)} <span class="sub">${net.connected ? '· connected' : saved ? '· saved' : ''}</span></span>
      <span class="sub">${net.secure ? '🔒 ' : ''}${net.signal}%</span></li>`;
  }).join('');
}
$('wifi-scan').addEventListener('click', () => send('wifi_scan'));

function renderBluetooth() {
  const b = state.bluetooth ?? {};
  $('bt-status').textContent = !b.available ? 'No Bluetooth adapter.' : b.message
    || 'Put a controller or headphones in pairing mode, then Search.';
  $('bt-scan').disabled = !b.available || b.scanning;
  $('bt-scan').textContent = b.scanning ? 'Searching…' : 'Search';
  $('bt-list').innerHTML = (b.devices ?? []).map((d) => {
    const actions = !d.paired ? [['bt_pair', 'Pair']] : [[d.connected ? 'bt_disconnect' : 'bt_connect', d.connected ? 'Disconnect' : 'Connect'], ['bt_remove', 'Forget']];
    return `<li><span>${escapeHtml(d.name)} <span class="sub">· ${escapeHtml(d.kind)}${d.connected ? ' · connected' : ''}</span></span>
      <span class="row" style="margin:0;flex:0 0 auto">${b.busy === d.path ? '<span class="sub">working…</span>'
        : actions.map(([cmd, text]) => `<button data-bt="${cmd}" data-path="${escapeHtml(d.path)}">${text}</button>`).join('')}</span></li>`;
  }).join('');
}
$('bt-scan').addEventListener('click', () => send('bt_scan'));
$('bt-list').addEventListener('click', (event) => {
  const { bt, path } = event.target.dataset;
  if (bt && (bt !== 'bt_remove' || confirm('Forget this device?'))) send(bt, { path });
});
$('wifi-list').addEventListener('click', (event) => {
  const li = event.target.closest('li[data-ssid]');
  if (!li) return;
  const ssid = li.dataset.ssid;
  if (li.dataset.saved === 'true') {
    if (confirm(`Forget ${ssid}? (Cancel connects to it instead.)`)) send('wifi_forget', { ssid });
    else send('wifi_connect', { ssid });
  } else if (li.dataset.secure === 'true') {
    const password = prompt(`Password for ${ssid}`);
    if (password) send('wifi_connect', { ssid, password });
  } else {
    send('wifi_connect', { ssid });
  }
});

$('update-check').addEventListener('click', () => send('update_check'));
$('update-apply').addEventListener('click', () => send('update_apply'));
$('update-reboot').addEventListener('click', () => { if (confirm('Reboot the box now?')) send('reboot'); });
$('snapshots').addEventListener('click', (event) => {
  const once = event.target.dataset.once, back = event.target.dataset.rollback;
  if (once && confirm(`Restart into snapshot ${once}? The next restart after that goes back to the normal system.`)) {
    send('snapshot_boot_once', { number: Number(once) });
  }
  if (back && confirm(`Replace the system with snapshot ${back} and restart? The current system is kept until the next rollback.`)) {
    send('snapshot_rollback', { number: Number(back) });
  }
});

$('volume').addEventListener('change', () => send('volume_set', { percent: Number($('volume').value) }));
$('outputs').addEventListener('click', (event) => {
  const li = event.target.closest('li[data-id]');
  if (li) send('audio_output', { id: Number(li.dataset.id) });
});
$('scales').addEventListener('click', (event) => {
  const scale = event.target.dataset.scale;
  if (scale) send('display_scale', { scale });
});
$('devices').addEventListener('click', (event) => {
  const id = event.target.dataset.revoke;
  if (id && confirm('Remove this phone? It will need to pair again.')) send('revoke_device', { id });
});
$('restart-session').addEventListener('click', () => {
  if (confirm('Restart the session? All apps are closed.')) send('restart_session');
});
$('reboot').addEventListener('click', () => { if (confirm('Reboot the box?')) send('reboot'); });

// ---- health -----------------------------------------------------------------
function duration(seconds) {
  const d = Math.floor(seconds / 86400), h = Math.floor(seconds / 3600) % 24, m = Math.floor(seconds / 60) % 60;
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}
const gb = (bytes) => `${(bytes / 1e9).toFixed(1)} GB`;

async function loadHealth() {
  try {
    const h = await (await fetch('/api/health')).json();
    const facts = {
      Version: `tvbox ${h.version}`,
      'CPU temperature': h.cpu_temp_c == null ? 'no sensor' : `${h.cpu_temp_c} °C`,
      Uptime: h.uptime_s == null ? '?' : duration(h.uptime_s),
      Load: h.load.map((l) => l.toFixed(2)).join('  '),
      'Free disk': `${gb(h.disk.free)} of ${gb(h.disk.total)}`,
      'Free memory': `${gb(h.memory.available)} of ${gb(h.memory.total)}`,
    };
    if (h.snapshot_boot) facts['Started from'] = 'a snapshot (backup)';
    $('health-summary').innerHTML = Object.entries(facts)
      .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    const unit = (u) => `<li><span>${escapeHtml(u.unit.replace(/\.service$/, ''))}
        <span class="sub">· ${escapeHtml(u.state)}${u.sub ? ` (${escapeHtml(u.sub)})` : ''}${u.restarts ? ` · ${u.restarts} restarts` : ''}</span></span>
        <span class="dot ${u.state === 'active' ? 'on' : u.state === 'failed' ? 'bad' : ''}"></span></li>`;
    $('health-services').innerHTML = [...h.services, ...h.system].map(unit).join('');
    $('health-restarts').innerHTML = h.restarts.map((r) => `<li><span>${escapeHtml(r.unit)}
        <span class="sub">· ${new Date(r.time * 1000).toLocaleString()}</span><br><span class="sub">${escapeHtml(r.message)}</span></span></li>`).join('')
      || '<li><span class="sub">None in the last 7 days</span></li>';
  } catch (err) {
    $('health-summary').textContent = `Could not load: ${err}`;
  }
}

// ---- bindings editor ----------------------------------------------------------
async function loadBindings() {
  const b = await (await fetch('/api/bindings')).json();
  $('bindings-text').value = b.user || '# Example: make Start send "k" in YouTube\n# [app.youtube]\n# start = "key:k"\n';
  $('bindings-defaults').textContent = b.defaults ?? '';
  showBindingErrors(b.errors, 'Bindings in use:');
}
function showBindingErrors(errors, okText) {
  $('bindings-result').className = errors.length ? 'bad' : 'ok';
  $('bindings-result').textContent = errors.length ? errors.join('\n') : okText;
}
async function submitBindings(checkOnly) {
  const reply = await fetch('/api/bindings', { method: 'POST',
    body: JSON.stringify({ text: $('bindings-text').value, check_only: checkOnly }) });
  const result = await reply.json();
  showBindingErrors(result.errors ?? [`error ${reply.status}`],
    checkOnly ? 'No problems found.' : 'Saved. The new bindings are active.');
}
$('bindings-check').addEventListener('click', () => submitBindings(true));
$('bindings-save').addEventListener('click', () => submitBindings(false));

// ---- tabs and connection ------------------------------------------------------
function showTab(name) {
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('section').forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  clearInterval(healthTimer);
  if (name === 'health') { loadHealth(); healthTimer = setInterval(loadHealth, 5000); }
  if (name === 'bindings') loadBindings();
  if (name === 'settings') { send('refresh'); send('snapshots'); }
}
$('tabs').addEventListener('click', (event) => { if (event.target.dataset.tab) showTab(event.target.dataset.tab); });

function render() {
  const current = state.services?.find((s) => s.focused);
  $('app-name').textContent = current?.name ?? (state.app === 'home' ? 'Home' : 'tvbox');
  $('link-state').textContent = state.type ? 'connected' : 'connecting…';
  $('link-state').classList.toggle('off', !state.type);
  renderApps();
  renderSettings();
  renderUpdates();
  renderWifi();
  renderBluetooth();
}

connect('phone', (msg) => {
  if (msg.type === 'state') { state = msg; render(); }
  else if (msg.type === 'error') console.warn(msg.error);
}, render);
