// Desplegable "Campañas": qué conjuntos de datos hay bajo raw_root, cuáles se
// usan, y renombrarlos.
//
// Una campaña es su propia raíz de datos (sus shot_id y su
// data/processed/<campaña>/ son suyos), y la web las une para trabajar. El
// nombre visible y el tilde de "usar" se guardan en el servidor
// (data/server/campaigns.json), no en el navegador: es configuración del
// conjunto de datos, no una preferencia de esta máquina.

const fmt = (n) => Number(n || 0).toLocaleString('es');

export function mountCampaigns(host, { onChange } = {}) {
  host.innerHTML = `
    <summary><span id="camp-summary">Campañas</span></summary>
    <p class="note">Cada campaña conserva sus propias marcas. Destildar una la saca de
    la tabla y de los promedios, sin borrar nada.</p>
    <div class="wrap"><table class="camp-table"><thead><tr>
      <th></th><th>Nombre</th><th class="num">Carpetas</th><th class="num">Capturas</th>
      <th>Directorio</th>
    </tr></thead><tbody id="camp-rows"></tbody></table></div>
    <p class="note" id="camp-status"></p>
  `;

  const elRows = host.querySelector('#camp-rows');
  const elSummary = host.querySelector('#camp-summary');
  const elStatus = host.querySelector('#camp-status');
  let items = [];

  function render() {
    const usadas = items.filter((c) => c.enabled).length;
    const capturas = items.reduce((a, c) => a + (c.enabled ? c.capture_count : 0), 0);
    elSummary.textContent =
      `Campañas — ${usadas} de ${items.length} en uso · ${fmt(capturas)} capturas`;

    elRows.innerHTML = items.map((c) => `
      <tr data-id="${c.id.replace(/"/g, '&quot;')}">
        <td><input type="checkbox" data-act="enabled" ${c.enabled ? 'checked' : ''}
              aria-label="Usar la campaña ${c.name}"></td>
        <td><input type="text" data-act="name" value="${c.name.replace(/"/g, '&quot;')}"
              class="camp-name" title="Nombre visible. Vaciarlo vuelve al nombre del directorio."></td>
        <td class="num">${fmt(c.folder_count)}</td>
        <td class="num">${fmt(c.capture_count)}</td>
        <td class="mono camp-path" title="${c.path.replace(/"/g, '&quot;')}">${c.processed_dir_name}</td>
      </tr>`).join('');
  }

  async function load() {
    try {
      const r = await fetch('/api/campaigns', { cache: 'no-store' }).then((x) => x.json());
      items = r.campaigns || [];
      render();
    } catch (_) {
      elStatus.textContent = 'no se pudo leer la lista de campañas';
    }
  }

  async function save(id, patch) {
    elStatus.textContent = 'guardando…';
    try {
      const res = await fetch('/api/campaigns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, ...patch }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const out = await res.json();
      const i = items.findIndex((c) => c.id === id);
      if (i >= 0 && out.campaign) items[i] = out.campaign;
      render();
      elStatus.textContent = 'guardado';
      if (onChange) onChange();
    } catch (err) {
      elStatus.textContent = `no se pudo guardar: ${err}`;
    }
  }

  elRows.addEventListener('change', (ev) => {
    const tr = ev.target.closest('tr[data-id]');
    if (!tr) return;
    const act = ev.target.dataset.act;
    if (act === 'enabled') save(tr.dataset.id, { enabled: ev.target.checked });
    else if (act === 'name') save(tr.dataset.id, { name: ev.target.value });
  });

  load();
  return { reload: load };
}
