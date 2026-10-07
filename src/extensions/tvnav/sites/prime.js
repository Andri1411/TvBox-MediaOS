// Prime Video (desktop site). Written from the site's public structure and
// not yet checked against a signed-in session: expect to adjust the selectors.
'use strict';

window.tvnavSite = {
  // The player opens as an overlay over the title's page (the address barely
  // changes); while it is up, its own keys apply: space play/pause, left/right
  // seek, Escape closes it.
  player: () => Boolean(document.querySelector('.atvwebplayersdk-overlays-container, .webPlayerSDKContainer video')),
  // Title cards and carousel arrows.
  candidates: '[data-testid="card"] a, [data-testid="packshot"] a, [data-testid$="-button"], .tst-hover-container a',
  // Hidden copies the carousels keep for their animation.
  ignore: '[aria-hidden="true"]',
};
