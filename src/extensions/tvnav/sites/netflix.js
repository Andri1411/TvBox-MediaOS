// Netflix (desktop site). Written from the site's public structure and not
// yet checked against a signed-in session: expect to adjust the selectors.
'use strict';

window.tvnavSite = {
  // On /watch the player has its own keys: space/Enter play-pause, left/right
  // seek, up/down volume, Escape or Back leaves.
  player: () => location.pathname.startsWith('/watch'),
  // Profile tiles, title cards and the row arrows are not all links/buttons.
  candidates: '.profile-link, .slider-item a, .title-card a, .handle, [data-uia$="-button"]',
  // Hidden duplicates Netflix keeps for its own carousel animation.
  ignore: '.slider-item--hidden, [aria-hidden="true"]',
};
