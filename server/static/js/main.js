import { initTheme } from './theme.js';
import * as capturas from './tabs/capturas.js';
import * as borrado from './tabs/borrado.js';

const TABS = {
  capturas: capturas,
  borrado: borrado,
};

let unmountCurrent = null;

function activateTab(name) {
  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.classList.toggle('active', btn.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll('.tab-panel')) {
    panel.hidden = panel.id !== `panel-${name}`;
  }

  if (unmountCurrent) {
    unmountCurrent();
    unmountCurrent = null;
  }
  const mod = TABS[name];
  if (mod) {
    const panel = document.getElementById(`panel-${name}`);
    unmountCurrent = mod.mount(panel) || null;
  }
}

function initTabs() {
  document.getElementById('tabs').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.tab-btn');
    if (!btn) return;
    activateTab(btn.dataset.tab);
  });
}

initTheme();
initTabs();
activateTab('capturas');
