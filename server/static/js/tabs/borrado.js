// Tab Borrado: los dos botones que hoy viven en el index (app.py:216-233).
// Ventana dedicada con filtros/banderas es §4 del plan; acá sólo se mudan.

export function mount(root) {
  root.innerHTML = `
    <section>
      <h2>Borrado</h2>
      <p class="sub">Nada se borra solo. El ZIP original se conserva salvo que lo pidas.</p>
      <div class="row" style="margin-bottom:12px">
        <button id="btn-del-sin-hammer-2">Borrar las capturas sin martillo</button>
      </div>
      <p class="sub">Para borrar una carpeta puntual, usá el botón "borrar" en la tabla
      de la tab Capturas.</p>
    </section>
  `;

  root.querySelector('#btn-del-sin-hammer-2').addEventListener('click', async () => {
    const r = await fetch('/api/dataset', { cache: 'no-store' }).then((x) => x.json());
    const sin = (r.folders || []).filter(
      (f) => f.captures.length && !f.captures.some((c) => c.has_hammer)).map((f) => f.folder);
    if (!sin.length) { alert('No hay capturas sin martillo.'); return; }
    if (!confirm(`¿Borrar ${sin.length} carpeta(s) sin martillo?\n\n` + sin.join('\n') +
                 `\n\nSe borran los datos extraídos; los ZIP originales se conservan.`)) return;
    const res = await fetch('/api/delete-sin-hammer', { method: 'POST' }).then((x) => x.json());
    alert(`Borradas: ${(res.deleted || []).length}`);
  });
}
