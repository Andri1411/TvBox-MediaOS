// Home screen: service tiles and the settings area. A normal window with
// keyboard focus, so the controller's keys (via the virtual keyboard) and a
// real keyboard both arrive here as key events.
'use strict';

const KEYS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
               Enter: 'ok', Escape: 'back', Backspace: 'back' };
const COLS = 5;

let tileFocus = 0;
let views = [];          // settings view stack; empty = tiles

function tiles() {
  const services = (state.services ?? []).map((s) => ({
    name: s.name, color: s.color,
    badge: s.state === 'running' ? 'running' : s.state === 'starting' ? 'starting…' : '',
    ok: () => send('launch', { id: s.id }),
  }));
  return [...services, { name: 'Settings', ok: () => push('main') }];
}

const VIEWS = {
  main: () => ({
    title: 'Settings',
    items: [
      { label: 'Audio output', value: currentOutput(), disabled: !state.sinks?.length, ok: () => push('outputs') },
      volumeItem(),
      { label: 'Display scale', value: state.display_scale ?? 'auto', ok: () => push('scale') },
      { label: 'Pair a phone', value: `${(state.devices ?? []).length} paired`, ok: () => push('pair') },
      { label: 'Wi-Fi', value: 'later version', disabled: true },
      { label: 'Bluetooth', value: 'later version', disabled: true },
      { label: 'Updates', value: updateSummary(), ok: () => push('updates') },
      { label: 'Snapshots', value: state.update?.booted_snapshot ? `running snapshot ${state.update.booted_snapshot}` : 'backups',
        ok: () => { send('snapshots'); push('snapshots'); } },
      { label: 'Restart session', ok: () => push('confirm_session') },
      { label: 'Reboot', ok: () => push('confirm_reboot') },
      { label: 'About', value: `tvbox ${state.version ?? ''}`, ok: () => push('about') },
    ],
  }),
  outputs: () => ({ title: 'Audio output', items: outputItems(pop),
    initial: Math.max(0, (state.sinks ?? []).findIndex((sink) => sink.default)) }),
  scale: () => {
    const scales = ['auto', '1', '1.25', '1.5', '2'];
    return {
      title: 'Display scale',
      items: scales.map((scale) => ({
        label: scale === 'auto' ? 'Automatic (2 on a 4K TV, else 1)' : `${scale}×`,
        value: scale === (state.display_scale ?? 'auto') ? 'current' : '',
        ok: () => send('display_scale', { scale }),
      })),
      initial: Math.max(0, scales.indexOf(state.display_scale ?? 'auto')),
    };
  },
  confirm_session: () => ({ title: 'Restart the session?',
    items: [{ label: 'Cancel', ok: pop }, { label: 'Restart session', value: 'closes all apps', ok: () => send('restart_session') }] }),
  confirm_reboot: () => ({ title: 'Reboot the box?',
    items: [{ label: 'Cancel', ok: pop }, { label: 'Reboot', ok: () => send('reboot') }] }),
  pair: () => ({
    title: 'Pair a phone',
    items: [
      { label: 'Done', ok: pop },
      ...(state.devices ?? []).map((d) => ({
        label: `Remove ${d.name}`, value: `paired ${new Date(d.created * 1000).toLocaleDateString()}`,
        ok: () => send('revoke_device', { id: d.id }),
      })),
    ],
    pair: true,
  }),
  updates: () => {
    const u = state.update ?? {};
    const items = [];
    if (u.status === 'available') {
      items.push({ label: `Install ${u.updates.length} update${u.updates.length === 1 ? '' : 's'}`,
        value: u.download_size ? `${(u.download_size / 1e6).toFixed(0)} MB` : '', ok: () => send('update_apply') });
      for (const p of u.updates.slice(0, 7)) items.push({ label: p.name, value: `${p.old} → ${p.new}`, disabled: true });
      if (u.updates.length > 7) items.push({ label: `and ${u.updates.length - 7} more`, disabled: true });
    } else if (u.status === 'done' && u.reboot_for?.length) {
      items.push({ label: 'Reboot now', value: 'needed for the update', ok: () => send('reboot') });
    }
    if (!['checking', 'applying'].includes(u.status)) items.push({ label: 'Check for updates', ok: () => send('update_check') });
    items.push({ label: 'Back', ok: pop });
    return { title: 'Updates', items, log: updateLog(u) };
  },
  snapshots: () => {
    const u = state.update ?? {};
    return {
      title: 'Snapshots',
      items: [{ label: 'Back', ok: pop }, ...(u.snapshots ?? []).filter((s) => s.type !== 'post').map((s) => ({
        label: `${snapshotTime(s.date)}`,
        value: s.number === u.booted_snapshot ? 'running now' : s.number === u.fallback ? 'before the last update'
          : snapshotLabel(s),
        ok: () => push('snapshot', s.number),
      }))],
      log: 'A snapshot is taken before every update. Starting one is safe: the next restart goes back to the normal system unless you keep it.',
    };
  },
  snapshot: (number) => ({
    title: `Snapshot ${number}`,
    items: [
      { label: 'Start it once', value: 'until the next restart', ok: () => push('confirm_snapshot', ['snapshot_boot_once', number]) },
      { label: 'Roll back to it', value: 'replaces the current system', ok: () => push('confirm_snapshot', ['snapshot_rollback', number]) },
      { label: 'Cancel', ok: pop },
    ],
  }),
  confirm_snapshot: ([cmd, number]) => ({
    title: cmd === 'snapshot_rollback' ? `Roll back to snapshot ${number} and restart?` : `Restart into snapshot ${number}?`,
    items: [{ label: 'Cancel', ok: pop }, { label: cmd === 'snapshot_rollback' ? 'Roll back and restart' : 'Restart', ok: () => send(cmd, { number }) }],
  }),
  backup: () => {
    const u = state.update ?? {};
    const when = u.fallback === u.booted_snapshot && u.fallback_time
      ? `before the update on ${new Date(u.fallback_time * 1000).toLocaleDateString()}` : `snapshot ${u.booted_snapshot}`;
    return {
      title: 'Started from a backup',
      items: [
        { label: 'Keep this backup', value: 'roll back for good', ok: () => push('confirm_snapshot', ['snapshot_rollback', u.booted_snapshot]) },
        { label: 'Try the updated system again', value: 'restart', ok: () => send('reboot') },
        { label: 'Decide later', ok: pop },
      ],
      log: `The box did not start properly after an update, so it started the backup made ${when}. Nothing is lost either way.`,
    };
  },
  about: () => ({ title: 'About', items: [],
    about: { Version: `tvbox ${state.version ?? ''}`, Name: state.hostname, Address: state.address || 'not connected',
             Kernel: state.kernel } }),
};

let pairing = null;          // {url, expires} while the pairing view is shown
let pairTimer = null;

async function startPairing() {
  pairing = { pending: true };
  try {
    const reply = await (await fetch('/api/pair/start', { method: 'POST' })).json();
    pairing = reply.ok ? reply : { error: reply.error };
  } catch (err) {
    pairing = { error: String(err) };
  }
  render();
}

function renderPairing(show) {
  $('pair').hidden = !show;
  clearInterval(pairTimer);
  if (!show) { pairing = null; return; }
  if (!pairing) { startPairing(); return; }
  if (pairing.pending) return;
  if (pairing.error) {
    $('pair-qr').hidden = true;
    $('pair-url').textContent = `Cannot pair: ${pairing.error}`;
    $('pair-expiry').textContent = '';
    return;
  }
  const qr = `/api/pair/qr.svg?url=${encodeURIComponent(pairing.url)}`;
  if ($('pair-qr').getAttribute('src') !== qr) $('pair-qr').src = qr;
  $('pair-qr').hidden = false;
  $('pair-url').textContent = pairing.url;
  $('pair-ip').textContent = pairing.url_ip !== pairing.url ? `If the phone can't open that: ${pairing.url_ip}` : '';
  const tick = () => {
    const left = Math.round(pairing.expires - Date.now() / 1000);
    if (left <= 0) { pairing = null; startPairing(); return; }       // a fresh code
    $('pair-expiry').textContent = `Code valid for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
  };
  tick();
  pairTimer = setInterval(tick, 1000);
}

function updateSummary() {
  const u = state.update ?? {};
  return { checking: 'checking…', applying: 'installing…', available: `${u.updates?.length} available`,
           none: 'up to date', done: u.reboot_for?.length ? 'installed, reboot needed' : 'installed',
           error: 'failed' }[u.status] ?? 'check now';
}

function updateLog(u) {
  if (u.status === 'error') return `Error: ${u.error}`;
  if (u.status === 'checking' || u.status === 'applying') return (u.log ?? []).slice(-6).join('\n') || 'Working…';
  if (u.status === 'none') return 'Everything is up to date.';
  if (u.status === 'available') {
    return `Nothing changes until you choose Install. A snapshot is taken first.${u.reboot_for?.length ? ' A restart is needed afterwards.' : ''}`;
  }
  if (u.status === 'done') return u.reboot_for?.length ? `Installed. Restart to use the new ${u.reboot_for.slice(0, 3).join(', ')}.` : 'Installed.';
  return 'Updates are never installed automatically.';
}

function snapshotTime(date) {
  return date ? new Date(`${date.replace(' ', 'T')}Z`).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '?';
}

function snapshotLabel(s) {
  const d = s.description ?? '';
  return d.startsWith('pacman') ? 'before an update' : d.slice(0, 40);
}

function push(name, arg) {
  if (name === 'main') send('refresh');     // audio devices may have changed
  views.push({ name, arg, focus: VIEWS[name](arg).initial ?? 0 });
  render();
}

function pop() {
  views.pop();
  render();
}

function render() {
  const settings = views.length > 0;
  $('tiles-view').hidden = settings;
  $('settings-view').hidden = !settings;
  if (settings) {
    const top = views[views.length - 1];
    const view = VIEWS[top.name](top.arg);
    top.focus = Math.max(0, Math.min(top.focus, view.items.length - 1));
    $('title').textContent = view.title;
    renderItems($('items'), view.items, top.focus);
    renderPairing(Boolean(view.pair));
    $('note').hidden = !view.log;
    $('note').textContent = view.log ?? '';
    $('about').hidden = !view.about;
    $('about').innerHTML = Object.entries(view.about ?? {})
      .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join('');
    return;
  }
  renderPairing(false);
  const all = tiles();
  tileFocus = Math.max(0, Math.min(tileFocus, all.length - 1));
  $('tiles').style.setProperty('--cols', Math.min(COLS, all.length));
  $('tiles').innerHTML = all.map((tile, i) => `
    <div class="tile ${i === tileFocus ? 'focus' : ''}" ${tile.color ? `style="--color:${escapeHtml(tile.color)}"` : ''}>
      ${tile.badge ? `<span class="badge">${tile.badge}</span>` : ''}${escapeHtml(tile.name)}
    </div>`).join('');
  const errors = state.service_errors ?? [];
  $('problem').hidden = !errors.length;
  $('problem').textContent = `services.toml has errors; using the defaults below it. ${errors[0] ?? ''}`;
}

function nav(button) {
  if (views.length) {
    const top = views[views.length - 1];
    if (button === 'back') { pop(); return; }
    top.focus = listNav(VIEWS[top.name](top.arg).items, top.focus, button);
    if (views[views.length - 1] === top) render();
    return;
  }
  const all = tiles();
  const cols = Math.min(COLS, all.length);
  const move = { left: -1, right: 1, up: -cols, down: cols }[button];
  if (move) {
    const next = tileFocus + move;
    if (next >= 0 && next < all.length) tileFocus = next;
    else if (button === 'down') tileFocus = all.length - 1;
  } else if (button === 'ok') {
    all[tileFocus]?.ok();
  }
  render();
}

let backupShown = false;

function onMessage(msg) {
  if (msg.type === 'state') {
    state = msg;
    // Started from a backup snapshot: say so and offer the choices, once.
    if (state.update?.booted_snapshot && !backupShown) {
      backupShown = true;
      views = [];
      push('backup');
    }
    render();
  } else if (msg.type === 'open') {
    views = [];
    if (msg.view === 'settings') push('main'); else render();
  }
}

function tick() {
  $('clock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

document.addEventListener('keydown', (event) => {
  const button = KEYS[event.key];
  if (button) { event.preventDefault(); nav(button); }
});
tick();
setInterval(tick, 10000);
connect('home', onMessage, render);
