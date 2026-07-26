// Tab Agrupamiento (§3.3): a qué grupo de dispersión pertenece cada carpeta.
// Porta `GroupingPanel` de field_review_app.py (:2018): a la izquierda la
// cantidad de grupos y las acciones de asignación, a la derecha la tabla de
// carpetas con selección múltiple.
//
// Cada grupo se procesa como un flujo completo (Enfase → Promedios → Waterfall
// → dispersión). Una carpeta sin asignación explícita pertenece al grupo 1.
import { mountCampaignPicker } from '../campaign_picker.js';

const fmtDist = (ds) => (ds && ds.length)
  ? ds.map((d) => `${Number(d).toFixed(0)}`).join(', ') + ' m'
  : '—';

function fmtFecha(mtime) {
  if (!mtime) return '—';
  const d = new Date(mtime * 1000);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card" id="ag-campaign"></div>

        <div class="card">
          <h2>Grupos de dispersión</h2>
          <div class="field-row">
            <label for="ag-count">Cantidad</label>
            <input type="number" id="ag-count" min="1" max="20" step="1" class="num-input">
          </div>

          <h2 style="margin-top:14px">Asignar las carpetas seleccionadas</h2>
          <div class="field-row">
            <label for="ag-target">Grupo</label>
            <input type="number" id="ag-target" min="1" step="1" value="1" class="num-input">
            <button type="button" id="ag-assign">Asignar</button>
          </div>
          <div class="toolbar">
            <button type="button" id="ag-all-1" class="btn-quiet">Todo a Grupo 1</button>
            <button type="button" id="ag-per-folder" class="btn-quiet">Una carpeta por grupo</button>
          </div>
          <p class="note" id="ag-status"></p>

          <p class="note">Cada carpeta pertenece a un grupo. Después elegís el grupo activo en
          Enfase y Promedios; cada grupo genera su propio waterfall y su propia imagen MASW, y en
          MASW se combinan las imágenes normalizadas con pesos.</p>
        </div>
      </div>

      <div class="ws-right">
        <section class="card pane-list">
          <div id="ag-summary" class="summary">cargando…</div>
          <div class="wrap table-scroll">
            <table class="rows-table"><thead><tr>
              <th class="num">Grupo</th><th>Carpeta</th><th class="num">Capturas</th>
              <th>Distancias</th><th class="num">OK</th><th>Fecha carpeta</th>
            </tr></thead><tbody id="ag-rows"></tbody></table>
          </div>
          <p class="note">Click selecciona; <kbd>Ctrl</kbd>+click suma o quita;
            <kbd>Shift</kbd>+click selecciona un rango.</p>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elRows = $('#ag-rows');
  let data = { group_count: 1, folders: [] };
  let campaign = '';
  let seleccion = new Set();
  let ultima = null;

  const picker = mountCampaignPicker($('#ag-campaign'), {
    onChange: (id) => { campaign = id; seleccion.clear(); load(); },
  });

  function render() {
    $('#ag-count').value = data.group_count;
    $('#ag-target').max = data.group_count;

    const porGrupo = new Map();
    for (const f of data.folders) porGrupo.set(f.group, (porGrupo.get(f.group) || 0) + 1);
    const reparto = [...porGrupo.entries()].sort((a, b) => a[0] - b[0])
      .map(([g, n]) => `G${g}: ${n}`).join(' · ');
    $('#ag-summary').textContent =
      `${data.folders.length} carpetas · ${data.group_count} grupo(s)` +
      (reparto ? ` · ${reparto}` : '') +
      (seleccion.size ? ` · ${seleccion.size} seleccionada(s)` : '');

    elRows.innerHTML = data.folders.map((f) => `
      <tr data-folder="${f.folder.replace(/"/g, '&quot;')}"${seleccion.has(f.folder) ? ' class="is-selected"' : ''}>
        <td class="num">${f.group}</td>
        <td class="ell" title="${f.folder}">${f.folder}</td>
        <td class="num">${f.captures}</td>
        <td class="ell" title="${fmtDist(f.distances)}">${fmtDist(f.distances)}</td>
        <td class="num">${f.ok}</td>
        <td class="mono">${fmtFecha(f.mtime)}</td>
      </tr>`).join('');
  }

  async function load() {
    try {
      data = await fetch(`/api/groups?campaign=${encodeURIComponent(campaign)}`,
        { cache: 'no-store' }).then((r) => r.json());
      // Una carpeta que ya no está no debe quedar seleccionada.
      const vivas = new Set(data.folders.map((f) => f.folder));
      seleccion = new Set([...seleccion].filter((f) => vivas.has(f)));
      render();
    } catch (err) {
      $('#ag-summary').textContent = `no se pudo leer: ${err}`;
    }
  }

  async function save(patch) {
    $('#ag-status').textContent = 'guardando…';
    try {
      const res = await fetch('/api/groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, ...patch }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      render();
      $('#ag-status').textContent = `guardado en ${data.path}`;
    } catch (err) {
      $('#ag-status').textContent = `no se pudo guardar: ${err}`;
    }
  }

  elRows.addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr[data-folder]');
    if (!tr) return;
    const folder = tr.dataset.folder;
    const orden = data.folders.map((f) => f.folder);
    if (ev.shiftKey && ultima) {
      const a = orden.indexOf(ultima);
      const b = orden.indexOf(folder);
      if (a >= 0 && b >= 0) {
        for (let i = Math.min(a, b); i <= Math.max(a, b); i++) seleccion.add(orden[i]);
      }
    } else if (ev.ctrlKey || ev.metaKey) {
      if (seleccion.has(folder)) seleccion.delete(folder);
      else seleccion.add(folder);
    } else {
      seleccion = new Set([folder]);
    }
    ultima = folder;
    render();
  });

  $('#ag-count').addEventListener('change', (ev) =>
    save({ group_count: Math.max(1, Math.min(20, Number(ev.target.value) || 1)) }));

  $('#ag-assign').addEventListener('click', () => {
    if (!seleccion.size) {
      $('#ag-status').textContent = 'no hay carpetas seleccionadas';
      return;
    }
    const grupo = Math.max(1, Math.min(data.group_count, Number($('#ag-target').value) || 1));
    const assign = {};
    for (const f of seleccion) assign[f] = grupo;
    save({ assign });
  });

  $('#ag-all-1').addEventListener('click', () => {
    const assign = {};
    for (const f of data.folders) assign[f.folder] = 1;
    save({ assign });
  });

  $('#ag-per-folder').addEventListener('click', () => {
    // Una carpeta por grupo: hace falta subir la cantidad de grupos a la vez,
    // o el clampeo del servidor las mandaría a todas al último grupo válido.
    const assign = {};
    data.folders.forEach((f, i) => { assign[f.folder] = i + 1; });
    save({ group_count: Math.min(20, Math.max(1, data.folders.length)), assign });
  });

  load();
  return {
    resume() { picker.reload(); load(); },
  };
}
