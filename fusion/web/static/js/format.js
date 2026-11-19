// Formatage des nombres à la française : espace insécable pour les milliers (1 000), virgule décimale.
// Exposé sur window.Fmt (navigateur) et en module CommonJS (tests Node).
(function (root) {
  // Intl fr-FR produit une espace fine (U+202F), presque invisible dans un canvas : on la remplace par une
  // espace insécable (U+00A0) pour un affichage « 1 000 » net partout, y compris sur les axes.
  const NARROW = / /g;
  const NBSP = ' ';
  const base = (d) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });
  const nf = (d) => ({ format: (v) => base(d).format(v).replace(NARROW, NBSP) });
  const Fmt = {
    int: (v) => (v == null ? '—' : nf(0).format(Math.round(v))),
    dec: (v, d = 1) => (v == null ? '—' : nf(d).format(v)),
    pct: (v, d = 1) => (v == null ? '—' : `${nf(d).format(v)}${NBSP}%`),
    eur: (v) => (v == null ? '—' : `${nf(0).format(Math.round(v))}${NBSP}€`),
    // montants compacts pour les grands nombres : 8,2 M€, 431 k€
    eurCompact: (v) => {
      if (v == null) return '—';
      const a = Math.abs(v);
      if (a >= 1e6) return `${nf(1).format(v / 1e6)}${NBSP}M€`;
      if (a >= 1e4) return `${nf(0).format(v / 1e3)}${NBSP}k€`;
      return `${nf(0).format(v)}${NBSP}€`;
    },
    month: (ym) => {
      const [y, m] = ym.split('-').map(Number);
      return new Intl.DateTimeFormat('fr-FR', { month: 'short', year: '2-digit' }).format(new Date(y, m - 1, 1));
    },
    date: (iso) => (iso ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'long' }).format(new Date(`${iso}T00:00:00`)) : '—'),
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Fmt;
  else root.Fmt = Fmt;
})(typeof window !== 'undefined' ? window : globalThis);
