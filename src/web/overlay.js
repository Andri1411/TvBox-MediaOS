// Overlay UI: system menu and OSD. Never has keyboard focus: while the menu
// is open the hub forwards controller navigation ("nav" messages) and this
// page keeps track of what is focused.
'use strict';

const $ = (id) => document.getElementById(id);
const OSD_MS = 1800;
const VOLUME_STEP = 5;

let state = {};
let ws = null;
let everConnected = false;
let views = [];          // stack of {name, focus}; last = shown
let osdTimer = null;

// The shell maps the window only while there is something to show, so the
// compositor does not blend an empty surface over the video.
function setVisible() {
  const visible = !$('menu').hidden || !$('osd').hidden;
  window.webkit?.messageHandlers?.tvbox?.postMessage({ visible });
}

function send(cmd, extra = {}) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd, ...extra }));
}

function bar(percent) {
  return `<span class="bar"><i style="width:${Math.max(0, Math.min(100, percent))}%"></i></span>`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

// ---- menu definition: views -> items ------------------------------------
// item: {label, value?, html?, disabled?, ok?, left?, right?}
const VIEWS = {
  main: () => ({
    title: 'Menu',
    items: [
      { label: 'Home', ok: () => send('home') },
      { label: 'Switch app', value: state.app ?? '', disabled: !state.apps?.length, ok: () => push('apps') },
      { label: 'Volume',
        html: state.volume == null ? 'no output' : `${bar(state.volume)} ${state.volume}`,
        cls: state.muted ? 'muted' : '',
        disabled: state.volume == null,
        left: () => send('volume', { delta: -VOLUME_STEP }),
        right: () => send('volume', { delta: VOLUME_STEP }) },
      { label: 'Mute', value: state.muted ? 'On' : 'Off', disabled: state.volume == null, ok: () => send('mute') },
      { label: 'Audio output', value: state.sinks?.find((s) => s.default)?.name ?? 'none',
        disabled: !state.sinks?.length, ok: () => push('outputs') },
      { label: 'Restart app', value: state.app ?? '', ok: () => send('restart_app') },
      { label: 'Mouse mode', value: state.mouse ? 'On' : 'Off', ok: () => send('mouse_toggle') },
      { label: 'Settings', value: 'later version', disabled: true },
      { label: 'Restart session', ok: () => push('confirm_session') },
      { label: 'Reboot', ok: () => push('confirm_reboot') },
    ],
  }),
  apps: () => ({
    title: 'Switch app',
    items: (state.apps ?? []).map((app) => ({
      label: app.name, value: app.focused ? 'current' : '', ok: () => send('switch_app', { id: app.id }),
    })),
    initial: Math.max(0, (state.apps ?? []).findIndex((app) => app.focused)),
  }),
  outputs: () => ({
    title: 'Audio output',
    items: (state.sinks ?? []).map((sink) => ({
      label: sink.name, value: sink.default ? 'current' : '', ok: () => { send('audio_output', { id: sink.id }); pop(); },
    })),
    initial: Math.max(0, (state.sinks ?? []).findIndex((sink) => sink.default)),
  }),
  confirm_session: () => ({
    title: 'Restart the session?',
    items: [{ label: 'Cancel', ok: pop }, { label: 'Restart session', value: 'closes all apps', ok: () => send('restart_session') }],
  }),
  confirm_reboot: () => ({
    title: 'Reboot the box?',
    items: [{ label: 'Cancel', ok: pop }, { label: 'Reboot', ok: () => send('reboot') }],
  }),
};

function push(name) {
  views.push({ name, focus: VIEWS[name]().initial ?? 0 });
  render();
}

function pop() {
  if (views.length > 1) { views.pop(); render(); } else send('close');
}

function render() {
  const open = state.overlay === 'menu' && views.length > 0;
  $('menu').hidden = !open;
  if (open) {
    const top = views[views.length - 1];
    const view = VIEWS[top.name]();
    top.focus = Math.max(0, Math.min(top.focus, view.items.length - 1));
    $('title').textContent = view.title;
    $('items').innerHTML = view.items.map((item, i) => `
      <li class="${i === top.focus ? 'focus' : ''} ${item.disabled ? 'disabled' : ''} ${item.cls ?? ''}">
        <span class="label">${escapeHtml(item.label)}</span>
        <span class="value">${item.html ?? escapeHtml(item.value)}</span>
      </li>`).join('');
    $('items').querySelector('.focus')?.scrollIntoView({ block: 'nearest' });
    const errors = state.config_errors ?? [];
    $('notice').hidden = !errors.length && state.input_connected;
    $('notice').textContent = !state.input_connected ? 'Input daemon is not running.'
      : `Bindings file has errors; the previous bindings stay active. ${errors[0] ?? ''}`;
    $('footer').textContent = [state.hostname, state.address, `tvbox ${state.version}`].filter(Boolean).join('  ·  ');
  }
  setVisible();
}

function nav(button) {
  if ($('menu').hidden) return;
  const top = views[views.length - 1];
  const items = VIEWS[top.name]().items;
  const item = items[top.focus];
  if (button === 'up' || button === 'down') {
    if (items.length) top.focus = (top.focus + (button === 'down' ? 1 : -1) + items.length) % items.length;
    render();
  } else if (button === 'back') {
    pop();
  } else if (item && !item.disabled && item[button]) {
    item[button]();           // ok / left / right
  }
}

function showOsd(msg) {
  const osd = $('osd');
  if (msg.kind === 'volume') {
    osd.className = msg.muted ? 'muted' : '';
    osd.innerHTML = `<span>${msg.muted ? 'Muted' : 'Volume'}</span>${bar(msg.volume)}<span class="num">${msg.volume}</span>`;
  } else {
    osd.className = '';
    osd.textContent = msg.text;
  }
  osd.hidden = false;
  clearTimeout(osdTimer);
  osdTimer = setTimeout(() => { osd.hidden = true; setVisible(); }, OSD_MS);
  setVisible();
}

function onMessage(msg) {
  if (msg.type === 'state') {
    const wasOpen = state.overlay === 'menu' && state.view === msg.view;
    state = msg;
    if (state.overlay === 'menu' && !wasOpen) {
      views = [];
      push(VIEWS[state.view] ? state.view : 'main');
    } else {
      if (state.overlay !== 'menu') views = [];
      render();
    }
  } else if (msg.type === 'nav') {
    nav(msg.button);
  } else if (msg.type === 'osd') {
    showOsd(msg);
  }
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    // The hub came back, possibly as a new version: start from a fresh page.
    if (everConnected) location.reload();
    everConnected = true;
  };
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onclose = () => {
    state = {};
    views = [];
    render();
    setTimeout(connect, 1000);
  };
}

connect();
