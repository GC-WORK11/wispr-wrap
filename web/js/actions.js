// actions.js — the @ picker. Owned by the actions lane (P5).
//
// INTERFACE (frozen — teach.js, library.js, macro.js build against this):
//   import { Actions } from './actions.js'
//   Actions.pick(opts?) -> Promise<actionObj | null>
//     - opens the actions sheet (#sheet-actions), resolves with the chosen
//       bound-action object per APP_API_CONTRACT §Bound action, null on cancel.
//     - opts: {forGloss?:string, current?:actionObj, title?:string}
//   Actions.catalog -> Promise<catalog> (cached GET /act; force via .refresh())
//   Actions.describe(actionObj) -> short human label (also used by library).
import { App } from './app.js';

export const Actions = {
  pick(opts) { return Promise.resolve(null); },
  catalog() { return Promise.resolve({ ok: false, apps: [], tools: [] }); },
  describe(a) { return a && a.label || ''; },
};
