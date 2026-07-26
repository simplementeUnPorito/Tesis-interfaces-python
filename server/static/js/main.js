import { initTheme } from './theme.js';
import * as capturas from './tabs/capturas.js';
import * as borrado from './tabs/borrado.js';
import * as masw from './tabs/masw.js';
import * as filtros from './tabs/filtros.js';
import * as agrupamiento from './tabs/agrupamiento.js';
import * as enfase from './tabs/enfase.js';
import * as promedios from './tabs/promedios.js';
import * as waterfall from './tabs/waterfall.js';

const TABS = {
  capturas: capturas,
  borrado: borrado,
  masw: masw,
  filtros: filtros,
  agrupamiento: agrupamiento,
  enfase: enfase,
  promedios: promedios,
  waterfall: waterfall,
};

// Cada tab se monta UNA vez y después sólo se muestra u oculta. Antes se
// desmontaba al cambiar de pestaña y se volvía a montar al volver: se perdía la
// fila seleccionada, el zoom, la zona auto y el scroll, y se repetían todos los
// pedidos al servidor. Lo que un tab necesita saber es si está a la vista, no
// si existe: para eso están `pause`/`resume`.
const mounted = new Map();
let currentTab = null;

function activateTab(name) {
  if (name === currentTab) return;

  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.classList.toggle('active', btn.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll('.tab-panel')) {
    panel.hidden = panel.id !== `panel-${name}`;
  }

  const prev = mounted.get(currentTab);
  if (prev && prev.pause) prev.pause();
  currentTab = name;

  const mod = TABS[name];
  if (!mod) return;

  let handle = mounted.get(name);
  if (!handle) {
    const panel = document.getElementById(`panel-${name}`);
    handle = mod.mount(panel) || {};
    mounted.set(name, handle);
  } else if (handle.resume) {
    handle.resume();
  }

  try {
    localStorage.setItem('geo-tab', name);
  } catch (_) { /* modo privado */ }
}

function initTabs() {
  document.getElementById('tabs').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.tab-btn');
    if (!btn) return;
    activateTab(btn.dataset.tab);
  });
}

function savedTab() {
  try {
    const name = localStorage.getItem('geo-tab');
    if (name && document.querySelector(`.tab-btn[data-tab="${name}"]`)) return name;
  } catch (_) { /* modo privado */ }
  return 'capturas';
}

initTheme();
initTabs();
activateTab(savedTab());
