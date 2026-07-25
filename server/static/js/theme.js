// Toggle claro/oscuro. Estructura calcada de initTheme/applyTheme en
// master/data/js/app.js, pero usando data-theme en :root en vez de una clase
// en <body> (así lo pide PORT_PLAN §2).
const STORAGE_KEY = 'geo-theme';

function systemPrefersDark() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function loadSaved() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch (_) {
    return null;
  }
}

function saveTheme(theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch (_) {
    // localStorage puede estar deshabilitado (modo privado, algunos navegadores móviles).
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

export function initTheme() {
  const saved = loadSaved();
  const theme = saved || (systemPrefersDark() ? 'dark' : 'light');
  applyTheme(theme);

  const btn = document.getElementById('btn-theme');
  if (btn) {
    btn.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      saveTheme(next);
    });
  }
}
