// Tab MASW: sólo enruta las 3 subtabs (Dispersion/Inversion/Perfil Vs).
// Dibujar dispersión, inversión y perfil es PORT_PLAN §3.5, otro ítem.

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

  return () => nav.removeEventListener('click', onClick);
}
