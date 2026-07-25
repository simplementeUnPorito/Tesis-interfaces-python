// Tab Capturas: mudanza literal de renderJobs/renderDataset (app.py:151-214).

const fmt = (n, d = 1) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

export function mount(root) {
  root.innerHTML = `
    <section>
      <h2>Subidas</h2>
      <div class="wrap"><table id="jobs"><thead><tr>
        <th>Cuándo</th><th>Archivo</th><th class="num">kB</th><th>Estado</th>
        <th>Carpeta</th><th class="num">Disparos</th><th class="num">Picks</th><th>Detalle</th>
      </tr></thead><tbody></tbody></table></div>
      <div id="jobs-empty" class="empty" hidden>Todavía no llegó ninguna captura.
        Desde la SPA del maestro: pestaña Captura → <code>Subir al server</code>.</div>
    </section>

    <section>
      <h2>Capturas <span id="ds-count" class="tag listo" hidden></span></h2>
      <p class="sub" style="margin:0 0 12px">Todo lo que llega se guarda, completo o no.
      Nada se borra solo: las capturas sin martillo quedan marcadas y se borran únicamente
      si vos lo pedís.</p>
      <div class="row" style="margin-bottom:12px">
        <button id="btn-del-sin-hammer">Borrar las capturas sin martillo</button>
      </div>
      <div id="dataset"></div>
    </section>
  `;

  async function tick() {
    try {
      const [jobs, ds] = await Promise.all([
        fetch('/api/jobs', { cache: 'no-store' }).then((r) => r.json()),
        fetch('/api/dataset', { cache: 'no-store' }).then((r) => r.json()),
      ]);
      renderJobs(root, jobs.jobs || []);
      renderDataset(root, ds);
    } catch (e) {
      // Sin conexión: no borrar lo que ya se mostraba, solo esperar el próximo tick.
    }
  }

  root.querySelector('#btn-del-sin-hammer').addEventListener('click', async () => {
    const r = await fetch('/api/dataset', { cache: 'no-store' }).then((x) => x.json());
    const sin = (r.folders || []).filter(
      (f) => f.captures.length && !f.captures.some((c) => c.has_hammer)).map((f) => f.folder);
    if (!sin.length) { alert('No hay capturas sin martillo.'); return; }
    if (!confirm(`¿Borrar ${sin.length} carpeta(s) sin martillo?\n\n` + sin.join('\n') +
                 `\n\nSe borran los datos extraídos; los ZIP originales se conservan.`)) return;
    const res = await fetch('/api/delete-sin-hammer', { method: 'POST' }).then((x) => x.json());
    alert(`Borradas: ${(res.deleted || []).length}`);
    tick();
  });

  window.borrarCarpeta = async (folder) => {
    if (!confirm(`¿Borrar la carpeta "${folder}"?\n\nSe borran los datos extraídos. ` +
                 `El ZIP original se conserva.`)) return;
    await fetch(`/api/delete?folder=${encodeURIComponent(folder)}`, { method: 'POST' });
    tick();
  };

  tick();
  const timer = setInterval(tick, 3000);
  return () => clearInterval(timer);
}

function renderJobs(root, rows) {
  const tb = root.querySelector('#jobs tbody');
  root.querySelector('#jobs-empty').hidden = rows.length > 0;
  tb.innerHTML = '';
  for (const j of rows) {
    const tr = document.createElement('tr');
    const detalle = j.error ? j.error.split('\n').slice(-1)[0]
                            : (j.log && j.log.length ? j.log[j.log.length - 1] : '');
    tr.innerHTML =
      `<td>${(j.created_at || '').replace('T', ' ').replace('+00:00', '')}</td>` +
      `<td>${j.filename}</td>` +
      `<td class="num">${(j.bytes / 1024).toFixed(0)}</td>` +
      `<td><span class="tag ${j.state}">${j.state}</span></td>` +
      `<td>${j.folder || '—'}</td>` +
      `<td class="num">${j.shots}</td>` +
      `<td class="num">${j.picks}</td>` +
      `<td>${detalle}</td>`;
    tb.appendChild(tr);
  }
}

function renderDataset(root, ds) {
  const host = root.querySelector('#dataset');
  const badge = root.querySelector('#ds-count');
  if (!ds.folders || !ds.folders.length) {
    host.innerHTML = '<div class="empty">Nada descubierto todavía en ' +
                     `<code>${ds.raw_root || ''}</code>.</div>`;
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  badge.textContent = `${ds.capture_count} capturas · ${ds.node_count} nodos · ` +
                      `${ds.shot_count} disparos MASW · ${ds.reviewed_count || 0} validados`;
  host.innerHTML = ds.folders.map((f) => `
    <h3 style="font-size:.9rem;margin:16px 0 6px">${f.folder}
      <button style="font-size:.75rem;font-weight:400;margin-left:8px"
              onclick="borrarCarpeta('${f.folder.replace(/'/g, "\\'")}')">borrar</button>
    </h3>
    <div class="wrap"><table><thead><tr>
      <th>Captura</th><th>Nodos</th><th class="num">fs</th><th class="num">seg</th>
      <th>Estado</th><th class="num">trigger (s)</th><th>validado</th>
    </tr></thead><tbody>
    ${f.captures.map((c) => {
      const nodos = c.nodes.map((n) =>
        `${n.role === 'hammer' ? '🔨' : n.role === 'geo' ? '📈' : '•'} ` +
        `${n.pcb_id || n.index}`).join(', ');
      const segs = Math.max(...c.nodes.map((n) => n.seconds || 0));
      // Sin martillo no hay primer arribo que picar: se dice, no se esconde.
      const estado = c.pickable
        ? '<span class="tag listo">completa</span>'
        : `<span class="tag pendiente">sin ${c.has_hammer ? 'geófono' : 'martillo'}</span>`;
      const p = c.pick;
      return `<tr>
        <td>${c.capture}</td>
        <td>${nodos}</td>
        <td class="num">${fmt(c.nodes[0] && c.nodes[0].fs, 0)}</td>
        <td class="num">${fmt(segs, 2)}</td>
        <td>${estado}</td>
        <td class="num">${p && p.trigger_s !== null ? fmt(p.trigger_s, 4) : '—'}</td>
        <td>${p && p.reviewed ? 'sí' : '—'}</td>
      </tr>`;
    }).join('')}
    </tbody></table></div>`).join('');
}
