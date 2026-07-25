// Corre antes del primer paint: sin esto, main.js (type=module, diferido)
// aplica el tema recién después de pintar y se ve un fogonazo del tema
// contrario en cada carga. Si cambia la clave 'geo-theme' acá, cambiarla
// también en theme.js (STORAGE_KEY): tienen que coincidir.
try {
  var t = localStorage.getItem('geo-theme');
  if (t) document.documentElement.dataset.theme = t;
} catch (e) { /* localStorage deshabilitado: queda prefers-color-scheme */ }
