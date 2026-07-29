// Cuarentena reversible: ningún control de este tab elimina archivos.

const LABELS = {
  sin_martillo: 'Sin martillo', sin_geofono: 'Sin geófono',
  sin_picking: 'Sin picking', no_validada: 'No validada',
  validada: 'Validada', rechazada: 'Rechazada',
  duplicada: 'Duplicada', sin_lfs: 'Sin subir/LFS',
  desactivada: 'Desactivada', ausente: 'Ausente en disco',
};
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <section class="card">
          <h2>Desactivación reversible</h2>
          <p class="note">Nada se borra: la acción aplica una bandera que excluye la carpeta
          de Capturas y del pipeline científico. Los raw, ZIP, marcas y estados se conservan
          y se pueden restaurar desde esta misma pantalla.</p>
          <div class="field-row"><label for="del-search">Buscar</label>
            <input id="del-search" type="search" placeholder="campaña o carpeta"></div>
          <div class="field-row"><label for="del-site">Sitio</label>
            <select id="del-site"><option value="">Todos</option></select></div>
          <div class="field-row"><label for="del-distance">Distancia</label>
            <select id="del-distance"><option value="">Todas</option></select></div>
          <div class="field-row"><label for="del-date-a">Fecha</label>
            <input id="del-date-a" type="date"><span>—</span><input id="del-date-b" type="date"></div>
          <div id="del-flags" class="filter-checks"></div>
          <div class="toolbar">
            <button id="del-visible" class="btn-quiet" type="button">Seleccionar visibles</button>
            <button id="del-none" class="btn-quiet" type="button">Limpiar</button>
          </div>
          <div class="toolbar">
            <button id="del-run" type="button" disabled>Previsualizar y desactivar</button>
            <button id="del-restore" class="btn-quiet" type="button" disabled>Restaurar</button>
          </div>
          <p id="del-status" class="note"></p>
        </section>
      </div>
      <div class="ws-right">
        <section class="card">
          <div class="viewer-head"><h2>Carpetas</h2>
            <div id="del-summary" class="viewer-meta">cargando…</div></div>
          <div class="wrap table-scroll deletion-table-wrap">
            <table class="rows-table"><thead><tr>
              <th></th><th>Sitio</th><th>Carpeta</th><th>Fecha</th>
              <th>Distancia</th><th class="num">Capturas</th><th>Estados</th>
            </tr></thead><tbody id="del-rows"></tbody></table>
          </div>
        </section>
      </div>
    </div>`;

  const $ = (q) => root.querySelector(q);
  let rows = [];
  let flags = [];
  const selected = new Set();

  function activeFlags() {
    return [...root.querySelectorAll('.del-flag:checked')].map((el) => el.value);
  }
  function filtered() {
    const search = $('#del-search').value.trim().toLocaleLowerCase('es');
    const site = $('#del-site').value;
    const distance = $('#del-distance').value;
    const a = $('#del-date-a').value;
    const b = $('#del-date-b').value;
    const required = activeFlags();
    return rows.filter((row) => {
      const text = `${row.campaign_name} ${row.folder}`.toLocaleLowerCase('es');
      return (!search || text.includes(search))
        && (!site || row.campaign === site)
        && (!distance || row.distance_group === distance)
        && (!a || row.date >= a) && (!b || row.date <= b)
        && required.every((flag) => row.flags.includes(flag));
    });
  }

  function render() {
    const visible = filtered();
    const selectedRows = rows.filter((row) => selected.has(row.key));
    $('#del-run').disabled = !selectedRows.some((row) => !row.disabled);
    $('#del-restore').disabled = !selectedRows.some((row) => row.disabled);
    $('#del-summary').textContent =
      `${visible.length} visibles de ${rows.length} · ${selected.size} seleccionadas`;
    let previous = '';
    $('#del-rows').innerHTML = visible.map((row) => {
      const group = `${row.campaign_name} · ${row.distance_group}`;
      const header = group === previous ? '' :
        `<tr class="group-row"><td colspan="7">${esc(group)}</td></tr>`;
      previous = group;
      return header + `<tr data-key="${esc(row.key)}">
        <td><input class="del-select" type="checkbox" ${selected.has(row.key) ? 'checked' : ''}></td>
        <td>${esc(row.campaign_name)}</td><td class="mono">${esc(row.folder)}</td>
        <td>${esc(row.date)}</td><td>${esc(row.distance_group)}</td>
        <td class="num">${row.captures}</td>
        <td>${row.flags.map((f) => `<span class="status-chip">${esc(LABELS[f] || f)}</span>`).join(' ')}</td>
      </tr>`;
    }).join('');
  }

  async function load() {
    $('#del-status').textContent = 'escaneando…';
    try {
      const r = await fetch('/api/deletion', { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      rows = data.rows || [];
      flags = (data.flags || []).filter((f) => f !== 'sin_lfs' || data.lfs_available);
      $('#del-flags').innerHTML = flags.map((flag) =>
        `<label class="control-check"><input class="del-flag" type="checkbox" value="${esc(flag)}">
          ${esc(LABELS[flag] || flag)} <span class="num">(${data.counts?.[flag] || 0})</span></label>`).join('');
      const sites = [...new Map(rows.map((r) => [r.campaign, r.campaign_name])).entries()];
      $('#del-site').innerHTML = '<option value="">Todos</option>' + sites.map(([id, name]) =>
        `<option value="${esc(id)}">${esc(name)}</option>`).join('');
      const distances = [...new Set(rows.map((r) => r.distance_group))].sort();
      $('#del-distance').innerHTML = '<option value="">Todas</option>' + distances.map((d) =>
        `<option value="${esc(d)}">${esc(d)}</option>`).join('');
      $('#del-status').textContent = data.lfs_available
        ? 'listo; estado Git/LFS disponible' : 'listo; esta raíz no es un repositorio Git';
      render();
    } catch (err) {
      $('#del-status').textContent = `no se pudo armar la lista: ${err}`;
    }
  }

  root.addEventListener('change', (ev) => {
    if (ev.target.classList.contains('del-select')) {
      const key = ev.target.closest('tr[data-key]')?.dataset.key;
      if (key) ev.target.checked ? selected.add(key) : selected.delete(key);
    }
    render();
  });
  for (const id of ['#del-search', '#del-site', '#del-distance', '#del-date-a', '#del-date-b']) {
    $(id).addEventListener(id === '#del-search' ? 'input' : 'change', render);
  }
  $('#del-visible').addEventListener('click', () => {
    for (const row of filtered()) selected.add(row.key);
    render();
  });
  $('#del-none').addEventListener('click', () => { selected.clear(); render(); });

  async function applyAction(action) {
    const restoring = action === 'restore';
    const keys = rows
      .filter((row) => selected.has(row.key) && Boolean(row.disabled) === restoring)
      .map((row) => row.key);
    if (!keys.length) return;
    $('#del-run').disabled = true;
    $('#del-restore').disabled = true;
    $('#del-status').textContent = 'generando previsualización exacta…';
    try {
      const previewRes = await fetch('/api/deletion/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys, action }),
      });
      const preview = await previewRes.json();
      if (!previewRes.ok) throw new Error(preview.detail || `HTTP ${previewRes.status}`);
      const list = preview.rows.map((r) =>
        `${r.campaign_name}/${r.folder} — ${r.captures} captura(s)`).join('\n');
      const verb = restoring ? 'restaurarán' : 'desactivarán';
      const note = restoring
        ? '\n\nVolverán a estar disponibles en el pipeline.'
        : '\n\nNo se borrará ningún archivo. Raw, ZIP y anotaciones se conservan.';
      if (!confirm(`Se ${verb} ${preview.count} carpetas y ${preview.captures} capturas:\n\n${list}${note}`)) {
        $('#del-status').textContent = 'operación cancelada; no se modificó ninguna bandera';
        return;
      }
      const endpoint = restoring ? '/api/deletion/restore' : '/api/deletion/disable';
      const actionRes = await fetch(endpoint, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: preview.token, keys }),
      });
      const outcome = await actionRes.json();
      if (!actionRes.ok) throw new Error(outcome.detail || `HTTP ${actionRes.status}`);
      for (const key of keys) selected.delete(key);
      const changed = restoring ? outcome.restored : outcome.disabled;
      await load();
      $('#del-status').textContent =
        `${restoring ? 'restauradas' : 'desactivadas'} ${changed?.length || 0}; `
        + `sin cambios ${outcome.unchanged?.length || 0}; errores ${outcome.errors?.length || 0}. `
        + 'No se borró ningún archivo.';
    } catch (err) {
      $('#del-status').textContent = `no se cambió ninguna bandera: ${err}`;
    } finally {
      render();
    }
  }

  $('#del-run').addEventListener('click', () => applyAction('disable'));
  $('#del-restore').addEventListener('click', () => applyAction('restore'));

  load();
  return { resume() { load(); } };
}
