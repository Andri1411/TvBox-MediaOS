// Relays "a text field got/lost focus" from the pages to the tvbox hub, which
// opens or closes the on-screen keyboard. The hub accepts nothing else from
// this extension.
'use strict';

chrome.runtime.onMessage.addListener((message) => {
  if (typeof message?.textFocus !== 'boolean') return;
  // text/plain keeps this a "simple" request (no preflight).
  fetch('http://127.0.0.1:8080/api/cmd', {
    method: 'POST',
    body: JSON.stringify({ cmd: 'text_focus', focused: message.textFocus }),
  }).catch(() => {});       // hub not running: navigation still works
});
