/* fusion-data — rendu du tableau de bord à partir de la zone gold (/api/gold/<rubrique>).
   Chart.js pour les graphiques ; carte en tuiles et matrices en HTML. Chaque graphique a une vue tableau
   (accessibilité, lecture exacte). Le filtre de source masque des séries sans jamais les recolorer. */
(() => {
  'use strict';
  const main = document.getElementById('contenu');
  if (!main || !document.querySelector('.tabs')) return;
  const ROLE = main.dataset.role;
  const CSRF = main.dataset.csrf;
  const SRC_LABEL = { AB: 'AbAssurance', AP: 'AssurePlus' };
  const state = { section: 'synthese', source: 'ALL', cache: {}, charts: [] };

  // ------------------------------------------------------------------ utilitaires
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const palette = () => ({
    AB: css('--ab'), AP: css('--ap'), both: css('--both'), ink: css('--ink'), ink2: css('--ink-2'),
    muted: css('--muted'), rule: css('--rule'), soft: css('--rule-soft'), surface: css('--surface'),
    critical: css('--critical'), warning: css('--warning'), seq: [0, 1, 2, 3, 4, 5, 6].map((i) => css(`--seq-${i}`)),
  });
  const h = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'style') node.style.cssText = v; // CSSOM : autorisé par la CSP (style-src 'self'), l'attribut ne l'est pas
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(c));
    return node;
  };
  /** Encre lisible sur un fond donné, d'après sa luminance relative (WCAG), quel que soit le thème. */
  const inkOn = (hex) => {
    const c = hex.replace('#', '').match(/../g).map((x) => {
      const v = Number.parseInt(x, 16) / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.25 ? '#1f1c17' : '#fffdf9';
  };
  const visible = (src) => state.source === 'ALL' || state.source === src;
  const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);

  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...opts });
    if (res.status === 401) { location.href = '/connexion'; throw new Error('session expirée'); }
    if (!res.ok) throw new Error(`${res.status} ${(await res.json().catch(() => ({}))).detail || ''}`);
    return res.json();
  }

  // ------------------------------------------------------------------ Chart.js : réglages communs
  function chartDefaults(p) {
    const C = window.Chart;
    C.defaults.font.family = getComputedStyle(document.body).fontFamily;
    C.defaults.font.size = 12;
    C.defaults.color = p.muted;
    C.defaults.borderColor = p.soft;
    C.defaults.animation = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 250 };
    C.defaults.plugins.legend.display = false; // légende HTML au-dessus du graphique
    Object.assign(C.defaults.plugins.tooltip, {
      backgroundColor: p.ink, titleColor: p.surface, bodyColor: p.surface, cornerRadius: 0, padding: 8,
      displayColors: true, boxWidth: 8, boxHeight: 8, boxPadding: 4,
    });
  }

  function scales({ horizontal = false, stacked = false, money = false, pct = false, max } = {}) {
    const p = palette();
    const valueTicks = { callback: (v) => (money ? Fmt.eurCompact(v) : pct ? `${v} %` : Fmt.int(v)), maxTicksLimit: 6 };
    const value = { beginAtZero: true, stacked, grid: { color: p.soft, drawTicks: false }, border: { display: false },
      ticks: { ...valueTicks, padding: 6 }, max };
    const cat = { stacked, grid: { display: false }, border: { color: p.ink }, ticks: { autoSkip: true, maxRotation: 0 } };
    return horizontal ? { x: value, y: cat } : { x: cat, y: value };
  }

  function tooltipValue(money, pct) {
    return (ctx) => {
      const v = ctx.parsed[ctx.chart.options.indexAxis === 'y' ? 'x' : 'y'];
      return ` ${ctx.dataset.label} : ${money ? Fmt.eur(v) : pct ? Fmt.pct(v) : Fmt.int(v)}`;
    };
  }

  /** Graphique à séries par source (AB / AP). block = {categories, libelles, series:{AB:[],AP:[]}} */
  function sourceChart(canvas, block, { type = 'bar', horizontal = false, stacked = false, money = false, labels,
    pct = false } = {}) {
    const p = palette();
    const datasets = ['AB', 'AP'].filter(visible).map((src) => ({
      label: SRC_LABEL[src], data: block.series[src], source: src,
      backgroundColor: p[src], borderColor: type === 'line' ? p[src] : p.surface,
      borderWidth: type === 'line' ? 2 : stacked ? { top: 2, right: 2 } : 0, borderSkipped: false,
      borderRadius: type === 'bar' ? 2 : 0, pointRadius: 0, pointHoverRadius: 4, tension: 0.2,
      maxBarThickness: 34, categoryPercentage: 0.78, barPercentage: 0.9,
    }));
    return track(new Chart(canvas, {
      type,
      data: { labels: labels || block.libelles || block.categories, datasets },
      options: {
        indexAxis: horizontal ? 'y' : 'x', maintainAspectRatio: false, responsive: true,
        interaction: { mode: 'index', intersect: false },
        scales: scales({ horizontal, stacked, money, pct }),
        plugins: { tooltip: { callbacks: { label: tooltipValue(money, pct) } } },
      },
    }));
  }

  /** Série unique (couleur passée explicitement). */
  function singleChart(canvas, labels, values, { color, colors, horizontal = false, money = false, pct = false,
    label = 'Valeur', refLine } = {}) {
    const p = palette();
    const datasets = [{ label, data: values, backgroundColor: colors || color || p.ink2, borderWidth: 0, borderRadius: 2,
      maxBarThickness: 40, categoryPercentage: 0.8, barPercentage: 0.9 }];
    if (refLine != null) {
      datasets.push({ type: 'line', label: refLine.label, data: labels.map(() => refLine.value), borderColor: p.ink,
        borderWidth: 1, borderDash: [4, 4], pointRadius: 0, fill: false });
    }
    return track(new Chart(canvas, {
      type: 'bar', data: { labels, datasets },
      options: { indexAxis: horizontal ? 'y' : 'x', maintainAspectRatio: false, responsive: true,
        scales: scales({ horizontal, money, pct }),
        plugins: { tooltip: { callbacks: { label: tooltipValue(money, pct) } } } },
    }));
  }

  function track(chart) { state.charts.push(chart); return chart; }

  // ------------------------------------------------------------------ composants HTML
  function figures(items) {
    return h('div', { class: 'figures' }, items.map((it) => h('div', { class: 'figure' },
      h('p', { class: 'label', text: it.label }),
      h('p', { class: 'value' }, it.value, it.unit ? h('small', { text: it.unit }) : null),
      it.split ? h('p', { class: 'split' }, Object.entries(it.split).filter(([s]) => visible(s)).map(([s, v]) =>
        h('span', {}, h('i', { class: `key key-${s.toLowerCase()}` }), `${SRC_LABEL[s]} ${v}`))) : null,
      it.note ? h('p', { class: 'note', text: it.note }) : null)));
  }

  function legend(items) {
    return h('div', { class: 'legend' }, items.map(([cls, text]) => h('span', {}, h('i', { class: `key ${cls}` }), text)));
  }
  const sourceLegend = () => legend(['AB', 'AP'].filter(visible).map((s) => [`key-${s.toLowerCase()}`, SRC_LABEL[s]]));

  /**
   * Panneau : titre, sous-titre, graphique + bascule vers la vue tableau.
   * table = {head: [...], rows: [[...]], numeric: [indices]} construit à la demande.
   */
  function panel({ title, sub, col = 6, height = '', draw, table, legendNode, reading, body }) {
    const chartBox = h('div', { class: `chart ${height}` });
    const tableBox = h('div', { class: 'table-wrap data-table', hidden: true });
    let showTable = false;
    const toggle = table ? h('button', { type: 'button', class: 'view-toggle', 'aria-pressed': 'false', onclick: () => {
      showTable = !showTable;
      toggle.setAttribute('aria-pressed', String(showTable));
      toggle.textContent = showTable ? 'Voir le graphique' : 'Voir les données';
      chartBox.hidden = showTable; tableBox.hidden = !showTable;
      if (showTable && !tableBox.firstChild) tableBox.append(dataTable(table()));
    } }, 'Voir les données') : null;
    const node = h('section', { class: `panel col-${col}` },
      h('div', { class: 'panel-head' }, h('h3', { text: title }), toggle),
      sub ? h('p', { class: 'sub', text: sub }) : null,
      legendNode || null,
      body || chartBox, table ? tableBox : null,
      reading ? h('p', { class: 'reading', text: reading }) : null);
    if (draw) requestAnimationFrame(() => draw(h('canvas', {}), chartBox));
    return node;
  }

  function dataTable({ head, rows, numeric = [], caption }) {
    return h('table', {},
      caption ? h('caption', { text: caption }) : null,
      h('thead', {}, h('tr', {}, head.map((c, i) => h('th', { class: numeric.includes(i) ? 'num' : null, text: c })))),
      h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c, i) => {
        const cell = h('td', { class: numeric.includes(i) ? 'num' : null });
        if (c instanceof Node) cell.append(c); else cell.textContent = c ?? '—';
        return cell;
      })))));
  }

  function seriesTable(block, fmt = Fmt.int, labels) {
    const srcs = ['AB', 'AP'].filter(visible);
    return () => ({
      head: ['', ...srcs.map((s) => SRC_LABEL[s]), 'Total'],
      numeric: srcs.map((_, i) => i + 1).concat(srcs.length + 1),
      rows: (labels || block.libelles || block.categories).map((lab, i) => [lab,
        ...srcs.map((s) => fmt(block.series[s][i])), fmt(sum(srcs.map((s) => block.series[s][i])))]),
    });
  }

  const mount = (canvasHost) => (canvas, box) => { box.append(canvas); canvasHost(canvas); };

  function grid(...panels) { return h('div', { class: 'grid' }, panels); }

  function head(title, text) { return h('div', { class: 'section-head' }, h('h2', { text: title }), h('p', { text })); }

  // ------------------------------------------------------------------ rubriques
  const RENDER = {};

  RENDER.synthese = (d) => {
    const s = d.synthese;
    const p = palette();
    const pf = s.portefeuille;
    const nodes = [
      head('Synthèse', 'Vue d’ensemble des deux bases après unification, dédoublonnage et contrôle qualité.'),
      figures([
        { label: 'Personnes uniques', value: Fmt.int(s.personnes), note: 'après dédoublonnage des deux bases' },
        { label: 'Comptes clients', value: Fmt.int(s.comptes.AB + s.comptes.AP),
          split: { AB: Fmt.int(s.comptes.AB), AP: Fmt.int(s.comptes.AP) } },
        { label: 'Clients des deux sociétés', value: Fmt.int(s.clients_communs), note: 'présents dans les deux bases' },
        { label: 'Contrats actifs', value: Fmt.int(s.contrats_actifs.AB + s.contrats_actifs.AP),
          split: { AB: Fmt.int(s.contrats_actifs.AB), AP: Fmt.int(s.contrats_actifs.AP) } },
        { label: 'Primes annualisées', value: Fmt.eurCompact(s.primes_annualisees) },
        { label: 'Encaissé sur 12 mois', value: Fmt.eurCompact(s.encaisse_12_mois) },
        { label: 'Ratio S/P 12 mois', value: Fmt.pct(s.ratio_sp_12_mois) },
        { label: 'Taux de doublons', value: Fmt.pct(s.taux_doublons), note: `${Fmt.int(s.anomalies_critiques)} anomalies critiques isolées` },
      ]),
      grid(
        panel({ title: 'Portefeuille unifié', sub: 'Personnes uniques selon la société d’origine', col: 5, height: 'short',
          legendNode: legend([['key-ab', 'AbAssurance seule'], ['key-ap', 'AssurePlus seule'], ['key-both', 'Les deux']]),
          draw: mount((c) => singleChart(c, pf.libelles, pf.valeurs, { colors: [p.AB, p.AP, p.both], horizontal: true,
            label: 'Personnes' })),
          table: () => ({ head: ['Origine', 'Personnes', 'Part'], numeric: [1, 2],
            rows: pf.libelles.map((l, i) => [l, Fmt.int(pf.valeurs[i]), Fmt.pct(100 * pf.valeurs[i] / sum(pf.valeurs))]) }),
          reading: `${Fmt.pct(100 * pf.valeurs[2] / (pf.valeurs[1] + pf.valeurs[2]))} des clients AssurePlus étaient déjà clients AbAssurance : c’est le potentiel de rapprochement commercial de la fusion.` }),
        panel({ title: 'Contrats actifs par branche', sub: 'Nombre de contrats au statut ACTIF', col: 7, height: 'short',
          legendNode: sourceLegend(), draw: mount((c) => sourceChart(c, s.branches, { stacked: true })),
          table: seriesTable(s.branches) }),
        panel({ title: 'Encaissements mensuels', sub: 'Paiements réussis, remboursements déduits', col: 12,
          legendNode: sourceLegend(),
          draw: mount((c) => sourceChart(c, s.encaissements, { type: 'line', money: true,
            labels: s.encaissements.categories.map(Fmt.month) })),
          table: seriesTable(s.encaissements, Fmt.eur, s.encaissements.categories.map(Fmt.month)) }),
      ),
    ];
    return nodes;
  };

  RENDER.clients = (d) => {
    const c = d.clients;
    const k = c.kpi;
    return [
      head('Répartition des clients', 'Comptes clients des deux bases (avant fusion) et personnes uniques (après dédoublonnage).'),
      figures([
        { label: 'Comptes clients', value: Fmt.int(k.comptes_total), split: { AB: Fmt.int(k.comptes.AB), AP: Fmt.int(k.comptes.AP) } },
        { label: 'Personnes uniques', value: Fmt.int(k.personnes) },
        { label: 'Clients communs', value: Fmt.int(k.clients_communs), note: `${Fmt.pct(k.taux_recouvrement_ap)} des clients AP` },
        { label: 'Personnes avec un contrat actif', value: Fmt.int(k.personnes_actives) },
        { label: 'Âge médian', value: Fmt.int(k.age_median), unit: 'ans',
          note: `${Fmt.int(sum(Object.values(k.sans_date_naissance)))} dates de naissance inexploitables` },
      ]),
      grid(
        panel({ title: 'Répartition géographique (France)', sub: 'Comptes clients par région, toutes sources ; survol pour le détail', col: 7,
          body: tileMap(c.par_region),
          table: seriesTable(c.par_region) }),
        panel({ title: 'Par pays', sub: 'Pays déduit du format du code postal', col: 5, height: 'short', legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_pays, { horizontal: true, stacked: true })), table: seriesTable(c.par_pays) }),
        panel({ title: 'Tranches d’âge', sub: 'Âge révolu à la date de référence', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_age)), table: seriesTable(c.par_age) }),
        panel({ title: 'Statut du compte', sub: 'Référentiel unifié ACTIF / INACTIF / RÉSILIÉ / PROSPECT', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_statut, { horizontal: true, stacked: true })), table: seriesTable(c.par_statut) }),
        panel({ title: 'Entrées en portefeuille par année', sub: 'Date de création du compte client', col: 8, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.cohortes, { type: 'line' })), table: seriesTable(c.cohortes) }),
        panel({ title: 'Multi-équipement', sub: 'Personnes selon leur nombre de contrats actifs', col: 4,
          draw: mount((x) => singleChart(x, c.multi_equipement.categories.map((v) => `${v} contrat${v === '1' ? '' : 's'}`),
            c.multi_equipement.valeurs, { color: palette().both, label: 'Personnes' })),
          table: () => ({ head: ['Contrats actifs', 'Personnes'], numeric: [1],
            rows: c.multi_equipement.categories.map((v, i) => [v, Fmt.int(c.multi_equipement.valeurs[i])]) }) }),
      ),
    ];
  };

  function tileMap(block) {
    const POS = {
      'Normandie': [1, 0], 'Hauts-de-France': [2, 0], 'Bretagne': [0, 1], 'Pays de la Loire': [1, 1],
      'Île-de-France': [2, 1], 'Grand Est': [3, 1], 'Nouvelle-Aquitaine': [1, 2], 'Centre-Val de Loire': [2, 2],
      'Bourgogne-Franche-Comté': [3, 2], 'Occitanie': [1, 3], 'Auvergne-Rhône-Alpes': [2, 3],
      "Provence-Alpes-Côte d'Azur": [3, 3], 'Outre-mer': [0, 4], 'Corse': [4, 4],
    };
    const p = palette();
    const totals = {};
    block.categories.forEach((r, i) => {
      totals[r] = ['AB', 'AP'].filter(visible).reduce((a, s) => a + block.series[s][i], 0);
    });
    const max = Math.max(1, ...Object.values(totals));
    const cells = [];
    const tip = document.getElementById('tooltip');
    for (let row = 0; row < 5; row++) {
      for (let col = 0; col < 5; col++) {
        const region = Object.keys(POS).find((r) => POS[r][0] === col && POS[r][1] === row);
        if (!region) { cells.push(h('div', { class: 'tile empty', 'aria-hidden': 'true' })); continue; }
        const i = block.categories.indexOf(region);
        const total = totals[region] || 0;
        const step = Math.min(6, Math.floor((total / max) * 6.999));
        const ab = i >= 0 ? block.series.AB[i] : 0;
        const ap = i >= 0 ? block.series.AP[i] : 0;
        const tile = h('div', { class: 'tile', style: `background:${p.seq[step]};color:${inkOn(p.seq[step])}`,
          tabindex: '0', role: 'img',
          'aria-label': `${region} : ${Fmt.int(total)} comptes (AbAssurance ${Fmt.int(ab)}, AssurePlus ${Fmt.int(ap)})` },
        h('span', { class: 't-name', text: region }), h('span', { class: 't-val', text: Fmt.int(total) }));
        const show = (ev) => {
          tip.hidden = false;
          tip.textContent = `${region} — ${Fmt.int(total)} comptes · AbAssurance ${Fmt.int(ab)} · AssurePlus ${Fmt.int(ap)}`;
          const r = (ev.currentTarget || tile).getBoundingClientRect();
          tip.style.left = `${Math.min(window.innerWidth - 270, r.left)}px`;
          tip.style.top = `${r.bottom + 6}px`;
        };
        tile.addEventListener('mouseenter', show); tile.addEventListener('focus', show);
        tile.addEventListener('mouseleave', () => { tip.hidden = true; }); tile.addEventListener('blur', () => { tip.hidden = true; });
        cells.push(tile);
      }
    }
    const scale = h('div', { class: 'scale' }, 'moins', p.seq.map((c) => h('i', { style: `background:${c}` })), 'plus',
      ` · maximum ${Fmt.int(max)} comptes`);
    return h('div', {}, h('div', { class: 'tilemap' }, cells), scale);
  }

  RENDER.contrats = (d) => {
    const c = d.contrats;
    const k = c.kpi;
    return [
      head('Statistiques contrats', 'Primes ramenées à une base annuelle : les primes mensuelles d’AssurePlus sont multipliées par 12.'),
      figures([
        { label: 'Contrats', value: Fmt.int(k.contrats), note: 'hors contrats orphelins isolés' },
        { label: 'Contrats actifs', value: Fmt.int(k.actifs_total), split: { AB: Fmt.int(k.actifs.AB), AP: Fmt.int(k.actifs.AP) } },
        { label: 'Primes annualisées', value: Fmt.eurCompact(k.primes_annualisees) },
        { label: 'Prime moyenne', value: Fmt.eur(k.prime_moyenne), note: 'par contrat actif et par an' },
        { label: 'Ancienneté moyenne', value: Fmt.dec(k.anciennete_moyenne_ans), unit: 'ans' },
        { label: 'Part de contrats résiliés', value: Fmt.pct(k.taux_resiliation) },
      ]),
      grid(
        panel({ title: 'Contrats actifs par branche', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_branche)), table: seriesTable(c.par_branche) }),
        panel({ title: 'Prime annuelle moyenne par branche', sub: 'Contrats actifs, en euros', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.prime_moyenne_branche, { money: true })),
          table: seriesTable(c.prime_moyenne_branche, Fmt.eur) }),
        panel({ title: 'Souscriptions mensuelles', sub: 'Contrats par mois de début d’effet, 24 derniers mois', col: 8,
          legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.souscriptions_mensuelles, { stacked: true,
            labels: c.souscriptions_mensuelles.categories.map(Fmt.month) })),
          table: seriesTable(c.souscriptions_mensuelles, Fmt.int, c.souscriptions_mensuelles.categories.map(Fmt.month)) }),
        panel({ title: 'Canal de distribution', sub: 'Contrats actifs', col: 4, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_canal, { stacked: true })), table: seriesTable(c.par_canal),
          reading: 'AbAssurance distribue en agence ; AssurePlus par courtier ou directement sur son application mobile.' }),
        panel({ title: 'Statut des contrats', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, c.par_statut, { horizontal: true, stacked: true })), table: seriesTable(c.par_statut) }),
        panel({ title: 'Premiers distributeurs', sub: 'Contrats actifs par agence ou courtier', col: 6,
          body: dataTable({ head: ['Code', 'Canal', 'Source', 'Contrats actifs'], numeric: [3],
            rows: c.top_distributeurs.filter((t) => visible(t.source)).map((t) => [t.code, t.canal, SRC_LABEL[t.source], Fmt.int(t.contrats_actifs)]) }) }),
      ),
    ];
  };

  RENDER.sinistres = (d) => {
    const s = d.sinistres;
    const k = s.kpi;
    const p = palette();
    const fr = s.score_fraude;
    return [
      head('Statistiques sinistres', 'Sinistres rattachés à un contrat valide ; montants estimés en euros.'),
      figures([
        { label: 'Sinistres', value: Fmt.int(k.sinistres) },
        { label: 'Survenus sur 12 mois', value: Fmt.int(k.survenus_12_mois), note: `fréquence ${Fmt.pct(k.frequence_12_mois)} des contrats actifs` },
        { label: 'Dossiers ouverts', value: Fmt.int(k.ouverts), note: 'ouverts ou en instruction' },
        { label: 'Charge estimée totale', value: Fmt.eurCompact(k.montant_total) },
        { label: 'Coût moyen', value: Fmt.eur(k.cout_moyen), note: `médiane ${Fmt.eur(k.cout_median)}` },
        { label: 'Taux de refus', value: Fmt.pct(k.taux_refus), note: 'sur dossiers clos' },
        { label: 'Suspicion de fraude', value: Fmt.int(k.suspicion_fraude), note: `score ≥ ${fr.seuil} (AssurePlus)` },
      ]),
      grid(
        panel({ title: 'Sinistres par mois de survenance', sub: '24 derniers mois', col: 8, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, s.mensuels, { stacked: true, labels: s.mensuels.categories.map(Fmt.month) })),
          table: seriesTable(s.mensuels, Fmt.int, s.mensuels.categories.map(Fmt.month)) }),
        panel({ title: 'Statut des dossiers', col: 4, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, s.par_statut, { horizontal: true, stacked: true })), table: seriesTable(s.par_statut) }),
        panel({ title: 'Sinistres par branche', col: 4, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, s.par_branche, { stacked: true })), table: seriesTable(s.par_branche) }),
        panel({ title: 'Coût moyen par branche', col: 4, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, s.cout_moyen_branche, { money: true })), table: seriesTable(s.cout_moyen_branche, Fmt.eur) }),
        panel({ title: 'Distribution des montants', sub: 'Nombre de sinistres par tranche de montant estimé', col: 4,
          legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, s.tranches_montant, { stacked: true, horizontal: true })), table: seriesTable(s.tranches_montant) }),
        panel({ title: 'Score de fraude (AssurePlus)', sub: fr.note, col: 12, height: 'short',
          legendNode: legend([['key-ap', 'Sous le seuil'], ['key-crit', `Au-dessus du seuil de ${fr.seuil}`]]),
          draw: mount((x) => singleChart(x, fr.categories, fr.valeurs, { label: 'Sinistres',
            colors: fr.categories.map((_, i) => (i * 10 >= fr.seuil ? p.critical : p.AP)) })),
          table: () => ({ head: ['Score', 'Sinistres'], numeric: [1], rows: fr.categories.map((c, i) => [c, Fmt.int(fr.valeurs[i])]) }) }),
      ),
    ];
  };

  RENDER.finances = (d) => {
    const f = d.finances;
    const k = f.kpi;
    const p = palette();
    const sp = f.ratio_sp_branche;
    return [
      head('Statistiques financières', 'Encaissements réels (paiements réussis) et ratio sinistres / primes sur 12 mois glissants.'),
      figures([
        { label: 'Encaissé sur 12 mois', value: Fmt.eurCompact(k.encaisse_12_mois),
          split: { AB: Fmt.eurCompact(k.encaisse_12_mois_source.AB), AP: Fmt.eurCompact(k.encaisse_12_mois_source.AP) } },
        { label: 'Primes annualisées', value: Fmt.eurCompact(k.primes_annualisees), note: 'portefeuille actif' },
        { label: 'Charge sinistres 12 mois', value: Fmt.eurCompact(k.charge_sinistres_12_mois) },
        { label: 'Ratio S/P 12 mois', value: Fmt.pct(k.ratio_sp_12_mois), note: 'sinistres / primes encaissées' },
        { label: 'Échecs de paiement', value: Fmt.pct(k.taux_echec_paiement_ap, 2), note: 'AssurePlus (AbAssurance ne trace pas les échecs)' },
      ]),
      grid(
        panel({ title: 'Encaissements mensuels', sub: 'En euros, paiements réussis moins remboursements', col: 12,
          legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, f.encaissements_mensuels, { stacked: true, money: true,
            labels: f.encaissements_mensuels.categories.map(Fmt.month) })),
          table: seriesTable(f.encaissements_mensuels, Fmt.eur, f.encaissements_mensuels.categories.map(Fmt.month)) }),
        panel({ title: 'Ratio S/P par branche', sub: 'Charge des sinistres survenus sur 12 mois / primes encaissées sur 12 mois',
          col: 6, legendNode: legend([['key-both', 'Ratio S/P'], ['key-rule', 'Équilibre technique (100 %)']]),
          draw: mount((x) => singleChart(x, sp.libelles, sp.valeurs, { color: p.both, pct: true, label: 'S/P',
            refLine: { value: 100, label: 'Équilibre' } })),
          table: () => ({ head: ['Branche', 'S/P'], numeric: [1], rows: sp.libelles.map((l, i) => [l, Fmt.pct(sp.valeurs[i])]) }),
          reading: 'Au-delà de 100 %, la branche coûte plus en sinistres qu’elle ne rapporte en primes, hors frais de gestion.' }),
        panel({ title: 'Primes annualisées par branche', sub: 'Portefeuille actif', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, f.primes_branche, { stacked: true, money: true })),
          table: seriesTable(f.primes_branche, Fmt.eur) }),
        panel({ title: 'Moyens de paiement', sub: 'Nombre de paiements réussis', col: 6, legendNode: sourceLegend(),
          draw: mount((x) => sourceChart(x, f.par_mode, { horizontal: true, stacked: true })), table: seriesTable(f.par_mode) }),
        panel({ title: 'Statut des transactions (AssurePlus)', sub: 'AB_PAIEMENT n’a pas de statut : un paiement présent est réputé encaissé',
          col: 6, height: 'short',
          draw: mount((x) => singleChart(x, f.statuts_paiement_ap.libelles, f.statuts_paiement_ap.valeurs,
            { color: p.AP, horizontal: true, label: 'Transactions' })),
          table: () => ({ head: ['Statut', 'Transactions'], numeric: [1],
            rows: f.statuts_paiement_ap.libelles.map((l, i) => [l, Fmt.int(f.statuts_paiement_ap.valeurs[i])]) }) }),
      ),
    ];
  };

  function heatTable(rowLabels, colLabels, values, { wide = false } = {}) {
    const p = palette();
    const flat = values.flat();
    const max = Math.max(1, ...flat);
    return h('table', { class: `matrix${wide ? ' wide' : ''}` },
      h('thead', {}, h('tr', {}, h('th', {}), colLabels.map((c) => h('th', { scope: 'col', text: c })))),
      h('tbody', {}, values.map((row, i) => h('tr', {}, h('th', { class: 'row', scope: 'row', text: rowLabels[i] }),
        row.map((v, j) => {
          const step = v ? Math.max(1, Math.min(6, Math.ceil((v / max) * 6))) : 0;
          return h('td', { style: `background:${p.seq[step]};color:${inkOn(p.seq[step])}`,
            title: `${rowLabels[i]} × ${colLabels[j]} : ${Fmt.int(v)}`, text: Fmt.int(v) });
        })))));
  }

  RENDER.doublons = (d) => {
    const x = d.doublons;
    const k = x.kpi;
    const ev = k.evaluation;
    const p = palette();
    const m = x.matrice_sources;
    const srcs = ['AB', 'AP'];
    const lbl = ['AbAssurance', 'AssurePlus'];
    const mat = (niv) => srcs.map((a) => srcs.map((b) => m[niv][a][b]));
    const hist = x.histogramme_scores;
    const nodes = [
      head('Matrice de doublons', 'Rapprochement des comptes clients intra et inter-bases : blocage, score pondéré, puis fusion des seules paires certaines.'),
      figures([
        { label: 'Comptes analysés', value: Fmt.int(k.comptes) },
        { label: 'Personnes uniques', value: Fmt.int(k.personnes), note: `${Fmt.int(k.comptes_fusionnes)} comptes fusionnés` },
        { label: 'Taux de doublons', value: Fmt.pct(k.taux_doublons) },
        { label: 'Paires certaines', value: Fmt.int(k.paires_certaines), note: `fusion automatique (score ≥ ${Fmt.dec(x.histogramme_scores.seuils.certain, 2)})` },
        { label: 'Paires probables', value: Fmt.int(k.paires_probables), note: 'à valider par le métier' },
        ev ? { label: 'Précision / rappel', value: `${Fmt.pct(100 * ev.certain.precision, 0)}`,
          note: `rappel ${Fmt.pct(100 * ev.certain.rappel, 0)} ; ${Fmt.pct(100 * ev.certain_probable.rappel, 0)} avec les probables` } : null,
      ].filter(Boolean)),
      grid(
        panel({ title: 'Doublons par couple de sources', sub: 'Nombre de paires ; la diagonale compte les doublons internes à une base',
          col: 6, body: h('div', { class: 'matrices' },
            h('div', {}, h('h4', { text: 'Paires certaines' }), heatTable(lbl, lbl, mat('CERTAIN'))),
            h('div', {}, h('h4', { text: 'Paires probables' }), heatTable(lbl, lbl, mat('PROBABLE')))),
          reading: `La case AbAssurance × AssurePlus mesure le recouvrement des portefeuilles : ${Fmt.int(m.CERTAIN.AB.AP)} paires certaines.` }),
        panel({ title: 'Critères concordants', sub: 'Pour chaque couple de critères, nombre de paires où les deux concordent',
          col: 6, body: heatTable(x.matrice_criteres.criteres, x.matrice_criteres.criteres, x.matrice_criteres.valeurs, { wide: true }),
          reading: 'La diagonale donne la fréquence de chaque critère ; hors diagonale, les combinaisons qui fondent la décision.' }),
        panel({ title: 'Distribution des scores', sub: 'Paires retenues (score ≥ 0,45), par pas de 0,05', col: 6,
          legendNode: legend([['key-seq3', `Probable (${Fmt.dec(hist.seuils.probable, 2)} à ${Fmt.dec(hist.seuils.certain, 2)})`], ['key-seq5', `Certain (≥ ${Fmt.dec(hist.seuils.certain, 2)})`]]),
          draw: mount((c) => singleChart(c, hist.categories, hist.valeurs, { label: 'Paires',
            colors: hist.categories.map((v) => (Number(v) >= hist.seuils.certain ? p.seq[5] : p.seq[3])) })),
          table: () => ({ head: ['Score ≥', 'Paires'], numeric: [1], rows: hist.categories.map((c, i) => [c, Fmt.int(hist.valeurs[i])]) }) }),
        panel({ title: 'Mesure de la qualité du rapprochement', sub: 'Comparaison à la vérité terrain du jeu de test', col: 6,
          body: ev ? dataTable({ head: ['Paires retenues', 'Détectées', 'Vrais positifs', 'Réelles', 'Précision', 'Rappel'],
            numeric: [1, 2, 3, 4, 5], rows: [['Certaines', ev.certain], ['Certaines et probables', ev.certain_probable]].map(([l, e]) =>
              [l, Fmt.int(e.paires_detectees), Fmt.int(e.vrais_positifs), Fmt.int(e.paires_reelles),
                Fmt.pct(100 * e.precision), Fmt.pct(100 * e.rappel)]),
            caption: 'Fusionner les seules paires certaines garantit de ne jamais fusionner deux personnes distinctes ; les probables partent en validation.' })
            : h('p', { text: 'Pas de vérité terrain disponible.' }) }),
      ),
    ];
    if (ROLE === 'admin') nodes.push(grid(pairsPanel()));
    return nodes;
  };

  function pairsPanel() {
    const box = h('div', { class: 'table-wrap' }, h('p', { class: 'loading', text: 'Chargement des paires…' }));
    const load = async (niveau) => {
      const data = await api(`/api/doublons/paires${niveau ? `?niveau=${encodeURIComponent(niveau)}` : ''}`);
      box.replaceChildren(dataTable({
        head: ['Score', 'Niveau', 'Compte A', 'Compte B', 'Naissance A / B', 'Code postal A / B', 'Critères concordants'],
        numeric: [0],
        rows: data.paires.map((r) => [Fmt.dec(r.score, 2), h('span', { class: `tag ${r.niveau.toLowerCase()}`, text: r.niveau.toLowerCase() }),
          `${r.uid_a} · ${r.nom_a}`, `${r.uid_b} · ${r.nom_b}`, `${r.naissance_a || '—'} / ${r.naissance_b || '—'}`,
          `${r.cp_a || '—'} / ${r.cp_b || '—'}`, r.criteres.join(', ')]),
        caption: `${Fmt.int(data.total)} paires ; 200 premières affichées, probables d’abord. Données personnelles : rôle administrateur uniquement.`,
      }));
    };
    const filters = h('div', { class: 'action-bar' },
      h('button', { type: 'button', class: 'btn secondary', onclick: () => load('PROBABLE') }, 'Paires probables (à valider)'),
      h('button', { type: 'button', class: 'btn secondary', onclick: () => load('CERTAIN') }, 'Paires certaines'),
      h('button', { type: 'button', class: 'btn secondary', onclick: () => load('') }, 'Toutes'));
    load('PROBABLE').catch((e) => box.replaceChildren(h('p', { class: 'empty', text: e.message })));
    return panel({ title: 'Paires à examiner', sub: 'File de validation métier (réservée à l’administrateur)', col: 12,
      body: h('div', {}, filters, box) });
  }

  RENDER.qualite = (d) => {
    const q = d.qualite;
    const exe = q.execution || {};
    const p = palette();
    const status = h('span', { class: 'status-line', text: '' });
    const btn = h('button', { type: 'button', class: 'btn', onclick: async () => {
      btn.disabled = true; status.textContent = 'Exécution en cours…';
      try {
        await api('/api/pipeline', { method: 'POST', headers: { 'X-CSRF-Token': CSRF } });
        const fail = (e) => { status.textContent = `Échec : ${e.message}`; btn.disabled = false; };
        const poll = async () => {
          const s = await api('/api/pipeline');
          if (s.etat === 'en_cours') { setTimeout(() => poll().catch(fail), 1500); return; }
          status.textContent = s.etat === 'termine' ? `Terminé : ${s.message}` : `Échec : ${s.message}`;
          btn.disabled = false; state.cache = {};
          if (s.etat === 'termine') setTimeout(() => go(state.section), 600);
        };
        poll().catch(fail);
      } catch (e) { status.textContent = `Échec : ${e.message}`; btn.disabled = false; }
    } }, 'Relancer le pipeline (incrémental)');
    const steps = exe.etapes || [];
    const sev = (g) => h('span', { class: `sev sev-${g}`, text: g });
    return [
      head('Qualité des données et exécution', 'Anomalies détectées par les règles DQ, réconciliation source ↔ silver et journal de la dernière exécution.'),
      h('div', { class: 'action-bar' }, btn, status,
        h('span', { class: 'status-line', text: `Dernière exécution : ${exe.run_id || '—'} · mode ${exe.mode || '—'} · bus ${exe.bus || '—'} · lac ${exe.lac || '—'} · ${Fmt.dec(exe.duree_totale_s, 2)} s` })),
      grid(
        panel({ title: 'Règles de qualité déclenchées', sub: 'Une ligne n’est jamais supprimée : elle est corrigée, isolée ou mise en quarantaine',
          col: 8, body: dataTable({ head: ['Règle', 'Libellé', 'Gravité', 'AB', 'AP', 'Total', 'Action'], numeric: [3, 4, 5],
            rows: q.regles.map((r) => [r.code, r.libelle, sev(r.gravite), Fmt.int(r.AB), Fmt.int(r.AP), Fmt.int(r.total), r.action]) }) }),
        panel({ title: 'Anomalies par gravité', col: 4, height: 'short',
          draw: mount((c) => singleChart(c, q.par_gravite.categories, q.par_gravite.valeurs, { label: 'Anomalies',
            colors: [p.critical, p.warning, p.ink2, p.muted], horizontal: true })),
          table: () => ({ head: ['Gravité', 'Anomalies'], numeric: [1],
            rows: q.par_gravite.categories.map((g, i) => [g, Fmt.int(q.par_gravite.valeurs[i])]) }),
          reading: `Orphelins isolés : ${Fmt.int(q.orphelins.contrat)} contrats, ${Fmt.int(q.orphelins.sinistre)} sinistres, ${Fmt.int(q.orphelins.paiement)} paiements.` }),
        panel({ title: 'Réconciliation source ↔ silver', sub: 'Lignes uniques par table ; tout écart bloque la publication du gold', col: 6,
          body: dataTable({ head: ['Table source', 'Source', 'Silver', 'Écart'], numeric: [1, 2, 3],
            rows: q.reconciliation.map((r) => [r.table_source, Fmt.int(r.lignes_source), Fmt.int(r.lignes_silver),
              h('span', { class: r.ecart ? 'ko' : 'ok', text: r.ecart ? Fmt.int(r.ecart) : '0 ✓' })]) }) }),
        panel({ title: 'Durée des étapes', sub: 'Dernière exécution, en secondes', col: 6, height: 'short',
          draw: mount((c) => singleChart(c, steps.map((s) => s.etape), steps.map((s) => s.duree_s), { horizontal: true,
            color: p.ink2, label: 'Secondes' })),
          table: () => ({ head: ['Étape', 'Durée (s)'], numeric: [1], rows: steps.map((s) => [s.etape, Fmt.dec(s.duree_s, 3)]) }) }),
      ),
    ];
  };

  // ------------------------------------------------------------------ navigation
  async function show(section) {
    state.section = section;
    document.querySelectorAll('.tabs button').forEach((b) => {
      if (b.dataset.section === section) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    try { history.replaceState(null, '', `#${section}`); } catch (e) { /* ignoré */ }
    state.charts.forEach((c) => c.destroy());
    state.charts = [];
    if (!state.cache[section]) {
      main.replaceChildren(h('p', { class: 'loading', text: 'Chargement…' }));
      try { state.cache[section] = await api(`/api/gold/${encodeURIComponent(section)}`); } catch (e) {
        main.replaceChildren(h('p', { class: 'empty', text: `Impossible de charger la rubrique : ${e.message}` }));
        return;
      }
    }
    chartDefaults(palette());
    main.replaceChildren(...RENDER[section](state.cache[section]));
  }

  /** Affiche une rubrique ; une erreur de rendu inattendue est montrée au lieu d'être perdue. */
  const go = (section) => show(section).catch((e) => {
    main.replaceChildren(h('p', { class: 'empty', text: `Affichage impossible : ${e.message}` }));
  });
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => go(b.dataset.section)));
  document.querySelectorAll('.segmented button').forEach((b) => b.addEventListener('click', () => {
    state.source = b.dataset.source;
    document.querySelectorAll('.segmented button').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
    go(state.section);
  }));
  const themeBtn = document.getElementById('theme-toggle');
  const isDark = () => document.documentElement.dataset.theme === 'dark'
    || (!document.documentElement.dataset.theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const syncThemeLabel = () => { themeBtn.textContent = isDark() ? 'Thème clair' : 'Thème sombre'; };
  themeBtn.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('fusion-theme', next); } catch (e) { /* ignoré */ }
    syncThemeLabel(); go(state.section);
  });
  syncThemeLabel();
  const refEl = document.getElementById('ref-date');
  if (refEl && /^\d{4}-\d{2}-\d{2}$/.test(refEl.textContent)) refEl.textContent = Fmt.date(refEl.textContent);
  // rubrique demandée dans l'URL (#…) : acceptée seulement si elle fait partie des rubriques connues
  const initial = location.hash.slice(1);
  const depart = Object.hasOwn(RENDER, initial) && document.querySelector(`[data-section="${initial}"]`) ? initial : 'synthese';
  if (main.querySelector('.loading')) go(depart);
})();
