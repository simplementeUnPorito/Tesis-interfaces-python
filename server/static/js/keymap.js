// Teclas reconfigurables.
//
// Los valores por defecto son los de la app PyQt (`_handle_review_key` :1534).
// Lo que el usuario cambie queda en localStorage: es una preferencia de esta
// máquina, no del conjunto de datos.

const STORE_KEY = 'geo-keymap';

export const ACTIONS = [
  { id: 'prev',      label: 'Muestra anterior',            def: 'w' },
  { id: 'next',      label: 'Muestra siguiente',           def: 's' },
  { id: 'zoneAMinus', label: 'Zona auto: borde izq. −',    def: 'a' },
  { id: 'zoneAPlus',  label: 'Zona auto: borde izq. +',    def: 'd' },
  { id: 'zoneBMinus', label: 'Zona auto: borde der. −',    def: 'ArrowLeft' },
  { id: 'zoneBPlus',  label: 'Zona auto: borde der. +',    def: 'ArrowRight' },
  { id: 'zoomIn',    label: 'Zoom + hammer',               def: 'ArrowUp' },
  { id: 'zoomOut',   label: 'Zoom − hammer',               def: 'ArrowDown' },
  { id: 'zoomInGeo',  label: 'Zoom + geófono',             def: 'q' },
  { id: 'zoomOutGeo', label: 'Zoom − geófono',             def: 'e' },
  { id: 'cycle',     label: 'Rotar estado',                def: ' ' },
  { id: 'flip',      label: 'Invertir la señal',           def: 'x' },
  { id: 'auto',      label: 'Recalcular el auto',          def: 'r' },
];

export function defaults() {
  return Object.fromEntries(ACTIONS.map((a) => [a.id, a.def]));
}

export function load() {
  const map = defaults();
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved && typeof saved === 'object') {
      for (const a of ACTIONS) {
        if (typeof saved[a.id] === 'string' && saved[a.id]) map[a.id] = saved[a.id];
      }
    }
  } catch (_) { /* modo privado, o basura guardada */ }
  return map;
}

export function save(map) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(map));
  } catch (_) { /* modo privado */ }
}

// Cómo se muestra una tecla. El espacio no se ve, así que se nombra.
export function keyLabel(key) {
  if (key === ' ') return 'Espacio';
  if (key === 'ArrowLeft') return '←';
  if (key === 'ArrowRight') return '→';
  if (key === 'ArrowUp') return '↑';
  if (key === 'ArrowDown') return '↓';
  return key.length === 1 ? key.toUpperCase() : key;
}

// Qué acción dispara un evento. Las letras se comparan sin distinguir
// mayúsculas: con Shift+↑ el navegador manda 'ArrowUp' igual, pero con Shift+w
// manda 'W'.
export function actionFor(map, ev) {
  const key = ev.key;
  const lower = key.length === 1 ? key.toLowerCase() : key;
  for (const [action, bound] of Object.entries(map)) {
    const b = bound.length === 1 ? bound.toLowerCase() : bound;
    if (b === lower) return action;
  }
  return null;
}

/**
 * Tabla editable de teclas. Al hacer click en una fila, la próxima tecla que se
 * apriete queda asignada. Si esa tecla ya estaba en uso, se libera de la otra
 * acción: dos acciones con la misma tecla dejarían una muerta.
 */
export function mountKeymapEditor(host, { onChange } = {}) {
  let map = load();
  let capturing = null;

  host.innerHTML = `
    <table class="keymap"><tbody>
      ${ACTIONS.map((a) => `
        <tr data-action="${a.id}">
          <td>${a.label}</td>
          <td class="num"><button type="button" class="key-btn" data-action="${a.id}"></button></td>
        </tr>`).join('')}
    </tbody></table>
    <div class="toolbar">
      <button type="button" id="keymap-reset" class="btn-quiet btn-sm">Restaurar las de la app</button>
      <span class="note" id="keymap-hint"></span>
    </div>
  `;

  const hint = host.querySelector('#keymap-hint');

  function render() {
    for (const btn of host.querySelectorAll('.key-btn')) {
      const isCapturing = capturing === btn.dataset.action;
      btn.textContent = isCapturing ? 'apretá una tecla…' : keyLabel(map[btn.dataset.action]);
      btn.classList.toggle('is-capturing', isCapturing);
    }
    hint.textContent = capturing ? 'Escape cancela' : '';
  }

  host.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.key-btn');
    if (btn) {
      capturing = btn.dataset.action;
      render();
      return;
    }
    if (ev.target.id === 'keymap-reset') {
      map = defaults();
      save(map);
      capturing = null;
      render();
      if (onChange) onChange(map);
    }
  });

  // En captura se traga la tecla: si no, asignar "s" también cambiaría de
  // muestra mientras se está configurando.
  function onKeyDown(ev) {
    if (!capturing) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.key === 'Escape') {
      capturing = null;
      render();
      return;
    }
    if (ev.key === 'Tab' || ev.key === 'Shift' || ev.key === 'Control' || ev.key === 'Alt') return;
    for (const [action, bound] of Object.entries(map)) {
      if (action !== capturing && bound === ev.key) map[action] = '';
    }
    map[capturing] = ev.key;
    save(map);
    capturing = null;
    render();
    if (onChange) onChange(map);
  }
  document.addEventListener('keydown', onKeyDown, true);   // captura: antes que el resto

  render();
  return {
    get map() { return map; },
    destroy() { document.removeEventListener('keydown', onKeyDown, true); },
  };
}
