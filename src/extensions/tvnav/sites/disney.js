// Disney+ (desktop site). Written from the site's public structure and not
// yet checked against a signed-in session: expect to adjust the selectors.
'use strict';

window.tvnavSite = {
  // The player has its own keys: space play-pause, left/right seek, Back leaves.
  player: () => /\/(play|video)\//.test(location.pathname),
  candidates: '[data-testid^="set-item"], [data-testid="profile-avatar"], [data-testid$="-button"]',
  ignore: '[aria-hidden="true"]',
};
