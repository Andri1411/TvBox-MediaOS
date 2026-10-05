// Shared by the overlay and the home screen: hub connection and list views.
'use strict';

const $ = (id) => document.getElementById(id);
const VOLUME_STEP = 5;

let state = {};
let ws = null;
let everConnected = false;

function send(cmd, extra = {}) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd, ...extra }));
}

function bar(percent) {
  return `<span class="bar"><i style="width:${Math.max(0, Math.min(100, percent))}%"></i></span>`;
}

// Safe in element content and in quoted attribute values. (Wi-Fi network
// and Bluetooth device names are chosen by whoever is nearby.)
function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// role: "overlay" or "home". onMessage(msg) for every hub message; onDrop()
// when the hub goes away.
function connect(role, onMessage, onDrop) {
  ws = new WebSocket(`ws://${location.host}/ws?role=${role}`);
  ws.onopen = () => {
    // The hub came back, possibly as a new version: start from a fresh page.
    if (everConnected) location.reload();
    everConnected = true;
  };
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onclose = () => {
    state = {};
    onDrop();
    setTimeout(() => connect(role, onMessage, onDrop), 1000);
  };
}

// ---- list views: items are {label, value?, html?, cls?, disabled?, ok?, left?, right?}
function renderItems(list, items, focus) {
  list.innerHTML = items.map((item, i) => `
    <li class="${i === focus ? 'focus' : ''} ${item.disabled ? 'disabled' : ''} ${item.cls ?? ''}">
      <span class="label">${escapeHtml(item.label)}</span>
      <span class="value">${item.html ?? escapeHtml(item.value)}</span>
    </li>`).join('');
  list.querySelector('.focus')?.scrollIntoView({ block: 'nearest' });
}

// Handles up/down/ok/left/right on a list; returns the new focus index.
function listNav(items, focus, button) {
  if (button === 'up' || button === 'down') {
    return items.length ? (focus + (button === 'down' ? 1 : -1) + items.length) % items.length : 0;
  }
  const item = items[focus];
  if (item && !item.disabled && item[button]) item[button]();
  return focus;
}

// Items both the system menu and the settings screen offer.
function volumeItem() {
  return { label: 'Volume',
    html: state.volume == null ? 'no output' : `${bar(state.volume)} ${state.volume}`,
    cls: state.muted ? 'muted' : '',
    disabled: state.volume == null,
    left: () => send('volume', { delta: -VOLUME_STEP }),
    right: () => send('volume', { delta: VOLUME_STEP }) };
}

function outputItems(done) {
  return (state.sinks ?? []).map((sink) => ({
    label: sink.name, value: sink.default ? 'current' : '',
    ok: () => { send('audio_output', { id: sink.id }); done(); },
  }));
}

function currentOutput() {
  return state.sinks?.find((s) => s.default)?.name ?? 'none';
}
