// Overlay UI: system menu, on-screen keyboard (keyboard.js) and OSD. Never
// has keyboard focus: while it is open the hub forwards controller navigation
// ("nav" messages) and this page keeps track of what is focused.
'use strict';

const OSD_MS = 1800;

let views = [];          // stack of {name, focus}; last = shown
let osdTimer = null;

// The shell maps the window only while there is something to show, so the
// compositor does not blend an empty surface over the video.
function setVisible() {
  const visible = !$('menu').hidden || !$('osd').hidden || !$('keyboard').hidden;
  window.webkit?.messageHandlers?.tvbox?.postMessage({ visible });
}

function currentService() {
  return state.services?.find((s) => s.id === state.app);
}

function openServices() {
  return (state.services ?? []).filter((s) => s.state !== 'stopped');
}

// ---- menu definition: views -> items ------------------------------------
const VIEWS = {
  main: () => ({
    title: 'Menu',
    items: [
      { label: 'Home', ok: () => send('home') },
      { label: 'Switch app', value: currentService()?.name ?? '', disabled: !state.services?.length, ok: () => push('apps') },
      volumeItem(),
      { label: 'Mute', value: state.muted ? 'On' : 'Off', disabled: state.volume == null, ok: () => send('mute') },
      { label: 'Audio output', value: currentOutput(), disabled: !state.sinks?.length, ok: () => push('outputs') },
      { label: 'Restart app', value: currentService()?.name ?? '', disabled: !currentService(), ok: () => send('restart_app') },
      { label: 'Close apps', value: openServices().length ? `${openServices().length} open` : '', disabled: !openServices().length, ok: () => push('close_apps') },
      { label: 'Mouse mode', value: state.mouse ? 'On' : 'Off', ok: () => send('mouse_toggle') },
      { label: 'Settings', ok: () => send('settings') },
      { label: 'Restart session', ok: () => push('confirm_session') },
      { label: 'Reboot', ok: () => push('confirm_reboot') },
    ],
  }),
  apps: () => {
    // running apps first, in the configured order
    const services = [...(state.services ?? [])].sort((a, b) => (a.state === 'stopped') - (b.state === 'stopped'));
    return {
      title: 'Switch app',
      items: services.map((s) => ({
        label: s.name, value: s.focused ? 'current' : s.state === 'stopped' ? '' : 'running',
        ok: () => send('switch_app', { id: s.id }),
      })),
      initial: Math.max(0, services.findIndex((s) => s.focused)),
    };
  },
  close_apps: () => {
    const open = openServices();
    const items = open.map((s) => ({
      label: s.name, value: s.focused ? 'current' : '', ok: () => send('stop_app', { id: s.id }),
    }));
    if (open.length > 1) items.push({ label: 'Close all', ok: () => send('stop_all_apps') });
    return {
      title: 'Close apps',
      items: items.length ? items : [{ label: 'No apps open', disabled: true }],
      initial: Math.max(0, open.findIndex((s) => s.focused)),
    };
  },
  outputs: () => ({
    title: 'Audio output',
    items: outputItems(pop),
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
  $('keyboard').hidden = state.overlay !== 'keyboard';
  if (state.overlay === 'keyboard') renderKeyboard();
  if (open) {
    const top = views[views.length - 1];
    const view = VIEWS[top.name]();
    top.focus = Math.max(0, Math.min(top.focus, view.items.length - 1));
    $('title').textContent = view.title;
    renderItems($('items'), view.items, top.focus);
    const errors = state.config_errors ?? [];
    $('notice').hidden = !errors.length && state.input_connected;
    $('notice').textContent = !state.input_connected ? 'Input daemon is not running.'
      : `Bindings file has errors; the previous bindings stay active. ${errors[0] ?? ''}`;
    $('footer').textContent = [state.hostname, state.address, `TvBox MediaOS ${state.version}`].filter(Boolean).join('  ·  ');
  }
  setVisible();
}

function nav(button) {
  if (state.overlay === 'keyboard') { keyboardNav(button); return; }
  if ($('menu').hidden) return;
  const top = views[views.length - 1];
  if (button === 'back') { pop(); return; }
  top.focus = listNav(VIEWS[top.name]().items, top.focus, button);
  if (views[views.length - 1] === top) render();
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
    if (msg.overlay === 'keyboard' && state.overlay !== 'keyboard') resetKeyboard();
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

connect('overlay', onMessage, () => { views = []; render(); });
