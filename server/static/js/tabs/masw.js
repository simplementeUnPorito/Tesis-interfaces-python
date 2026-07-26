// Tab MASW: enruta las 3 subtabs (Dispersion/Inversion/Perfil Vs) y monta la
// primera. Inversión y perfil siguen pendientes (PORT_PLAN §3.5, DUDAS #17):
// los backends de `masw_backends.py` tardan minutos y tienen que correr como
// trabajos del Pipeline, no dentro de un request.
import * as dispersion from './masw_dispersion.js';

function activateSubtab(root, name) {
  for (const btn of root.querySelectorAll('.subtab-btn')) {
    btn.classList.toggle('active', btn.dataset.subtab === name);
  }
  for (const panel of root.querySelectorAll('.subtab-panel')) {
    panel.hidden = panel.id !== `subpanel-${name}`;
  }
}

export function mount(root) {
  const nav = root.querySelector('#masw-subtabs');
  if (!nav) return null;

  const onClick = (ev) => {
    const btn = ev.target.closest('.subtab-btn');
    if (!btn) return;
    activateSubtab(root, btn.dataset.subtab);
  };
  nav.addEventListener('click', onClick);

  // La subtab de dispersión se monta una sola vez, igual que las tabs de
  // arriba: cambiar de subtab no puede tirar la imagen ya calculada.
  const panel = root.querySelector('#subpanel-dispersion');
  const sub = panel ? dispersion.mount(panel) : null;

  return {
    resume() { if (sub && sub.resume) sub.resume(); },
    destroy() { if (sub && sub.destroy) sub.destroy(); },
  };
}
