// Selector de campaña para las tabs que trabajan sobre una campaña entera
// (Agrupamiento, Enfase, Promedios), donde no hay una captura elegida de la
// cual deducirla.
//
// Existe porque cada campaña es su propia raíz de datos: sus grupos, offsets de
// enfase y promedios viven en su propio `data/processed/<campaña>/`. Mezclarlos
// no tendría sentido físico — son adquisiciones distintas.
//
// La elección se recuerda en localStorage, compartida entre esas tabs: pasar de
// Agrupamiento a Enfase no debería obligar a volver a elegir.
const STORE_KEY = 'geo-campaign';

export function currentCampaign() {
  try {
    return localStorage.getItem(STORE_KEY) || '';
  } catch (_) {
    return '';
  }
}

export function mountCampaignPicker(host, { onChange } = {}) {
  host.innerHTML = `
    <div class="field-row">
      <label for="camp-pick">Campaña</label>
      <select id="camp-pick"></select>
    </div>
    <p class="note" id="camp-pick-note"></p>
  `;
  const sel = host.querySelector('#camp-pick');
  const nota = host.querySelector('#camp-pick-note');
  let items = [];

  function elegida() {
    const guardada = currentCampaign();
    if (items.some((c) => c.id === guardada)) return guardada;
    // Por defecto, la campaña con más capturas: es la que se va a querer mirar.
    const mayor = items.slice().sort((a, b) => b.capture_count - a.capture_count)[0];
    return mayor ? mayor.id : '';
  }

  async function load() {
    try {
      const r = await fetch('/api/campaigns', { cache: 'no-store' }).then((x) => x.json());
      items = (r.campaigns || []).filter((c) => c.enabled);
      const actual = elegida();
      sel.innerHTML = items.map((c) =>
        `<option value="${c.id.replace(/"/g, '&quot;')}"${c.id === actual ? ' selected' : ''}>` +
        `${c.name} (${c.capture_count} capturas)</option>`).join('');
      nota.textContent = items.length > 1
        ? 'Cada campaña tiene sus propios grupos, enfases y promedios.'
        : '';
      try { localStorage.setItem(STORE_KEY, actual); } catch (_) { /* modo privado */ }
      return actual;
    } catch (err) {
      nota.textContent = `no se pudo leer la lista de campañas: ${err}`;
      return '';
    }
  }

  sel.addEventListener('change', () => {
    try { localStorage.setItem(STORE_KEY, sel.value); } catch (_) { /* modo privado */ }
    if (onChange) onChange(sel.value);
  });

  const listo = load().then((actual) => {
    if (onChange) onChange(actual);
    return actual;
  });

  return {
    get value() { return sel.value; },
    ready: listo,
    reload: () => load(),
  };
}
