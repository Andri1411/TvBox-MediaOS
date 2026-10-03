// On-screen keyboard for the overlay: a grid driven by the d-pad. Characters
// go to the hub ("type"), which types them into the focused app.
'use strict';

const BOTTOM = [
  { label: '⇧', layer: 'upper' }, { label: '#+=', layer: 'symbols' }, { label: 'áð', layer: 'accents' },
  { label: 'space', text: ' ', span: 4 }, { label: '@', text: '@' },
  { label: '⌫', key: 'backspace' }, { label: '↵', key: 'enter' },
];
const LAYERS = {
  lower: ['1234567890', 'qwertyuiop', "asdfghjkl'", 'zxcvbnm,.-'],
  upper: ['1234567890', 'QWERTYUIOP', 'ASDFGHJKL"', 'ZXCVBNM;:_'],
  symbols: ['!"#$%&/()=', '+-*?^~`´{}', '[]<>|\\_:;€', '@.,\'…£§°¿¡'],
  accents: ['áéíóúýþæöð', 'ÁÉÍÓÚÝÞÆÖÐ', 'äåøüßñçèàê', 'ÄÅØÜẞÑÇÈÀÊ'],
};

let kb = { layer: 'lower', row: 1, col: 0, typed: '' };

function resetKeyboard() {
  kb = { layer: 'lower', row: 1, col: 0, typed: '' };
}

// Rows of the current layer as [{label, text?, key?, layer?, span, start}].
function keyboardRows() {
  const rows = LAYERS[kb.layer].map((row) => [...row].map((ch) => ({ label: ch, text: ch })));
  rows.push(BOTTOM.map((key) => ({ ...key })));
  for (const row of rows) {
    let start = 0;
    for (const key of row) { key.span ??= 1; key.start = start; start += key.span; }
  }
  return rows;
}

function renderKeyboard() {
  const rows = keyboardRows();
  // what was typed in this session (the field itself may be hidden behind the keyboard)
  const shown = [...kb.typed];
  $('typed').textContent = shown.length > 44 ? `…${shown.slice(-43).join('')}` : kb.typed;
  $('typed').classList.toggle('empty', !kb.typed);
  $('keys').innerHTML = rows.map((row, r) => row.map((key, c) => `
    <span class="key ${r === kb.row && c === kb.col ? 'focus' : ''} ${key.layer === kb.layer ? 'active' : ''}"
          style="grid-column: span ${key.span}">${escapeHtml(key.label)}</span>`).join('')).join('');
}

function pressKey(key) {
  if (key.text) {
    send('type', { text: key.text });
    kb.typed += key.text;
    if (kb.layer === 'upper') kb.layer = 'lower';        // shift is one-shot
  } else if (key.key === 'backspace') {
    send('key', { key: 'backspace' });
    kb.typed = [...kb.typed].slice(0, -1).join('');
  } else if (key.key === 'enter') {
    send('key', { key: 'enter' });
    send('close');
  } else if (key.layer) {
    kb.layer = kb.layer === key.layer ? 'lower' : key.layer;
  }
}

function keyboardNav(button) {
  const rows = keyboardRows();
  const row = rows[kb.row];
  if (button === 'left' || button === 'right') {
    kb.col = (kb.col + (button === 'right' ? 1 : -1) + row.length) % row.length;
  } else if (button === 'up' || button === 'down') {
    const centre = row[kb.col].start + row[kb.col].span / 2;
    kb.row = (kb.row + (button === 'down' ? 1 : -1) + rows.length) % rows.length;
    const target = rows[kb.row];
    kb.col = target.reduce((best, key, i) =>
      Math.abs(key.start + key.span / 2 - centre) < Math.abs(target[best].start + target[best].span / 2 - centre) ? i : best, 0);
  } else if (button === 'ok') {
    pressKey(row[kb.col]);
  } else if (button === 'x') {
    pressKey({ key: 'backspace' });
  } else if (button === 'start') {
    pressKey({ key: 'enter' });
  } else if (button === 'back') {
    send('close');
    return;
  }
  renderKeyboard();
}
