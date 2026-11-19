// Applique le thème mémorisé avant le premier rendu (évite le flash de thème clair).
(function () {
  try {
    var t = localStorage.getItem('fusion-theme');
    if (t === 'dark' || t === 'light') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* stockage indisponible : thème du système */ }
})();
