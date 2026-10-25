// macro.js — macro builder + context rules editor. Owned by lane P5b.
//
// INTERFACE (frozen — actions.js, library.js, teach.js build against this):
//   import { Macro } from './macro.js'
//   Macro.edit(action?) -> Promise<actionObj | null>
//     - opens #sheet-macro, resolves a {kind:'macro'|'context',...} action or
//       the edited simple action; null on cancel.
//   Macro.describe(action) -> human label ("3-step macro", "context: 3 rules").
import { App } from './app.js';

export const Macro = {
  edit(action) { return Promise.resolve(null); },
  describe(a) { return a && a.label || ''; },
};
