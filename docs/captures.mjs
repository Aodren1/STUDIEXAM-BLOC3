// Captures d'écran du tableau de bord pour le dossier (Chrome ou Edge headless piloté par le protocole DevTools,
// aucune dépendance npm). Démarre le serveur local, se connecte avec les deux rôles et photographie chaque rubrique.
// Usage : node docs/captures.mjs        (depuis la racine du dépôt, pipeline déjà exécuté)
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = join(root, 'docs', 'captures');
const PORT = 8099;
const BASE = `http://127.0.0.1:${PORT}`;
const ACCOUNTS = { admin: process.env.FUSION_ADMIN_PASSWORD || 'Admin-Fusion-2026',
  analyste: process.env.FUSION_USER_PASSWORD || 'Analyste-Fusion-2026' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(OUT, { recursive: true });

function findBrowser() {
  return [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium']
    .find((p) => p && existsSync(p));
}

async function startServer() {
  const py = [join(root, '.venv', 'Scripts', 'python.exe'), join(root, '.venv', 'bin', 'python')].find(existsSync) || 'python';
  const proc = spawn(py, ['-m', 'fusion', 'serve', '--port', String(PORT)], { cwd: root, stdio: 'ignore' });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`${BASE}/api/sante`)).ok) return proc; } catch { /* pas encore prêt */ }
    await sleep(250);
  }
  throw new Error('serveur indisponible');
}

async function launch() {
  const profile = mkdtempSync(join(tmpdir(), 'captures-'));
  const proc = spawn(findBrowser(), ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--force-prefers-reduced-motion',
    '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' });
  const portFile = join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(100);
  const port = readFileSync(portFile, 'utf8').split('\n')[0];
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  return { proc, profile, ws: pages.find((p) => p.type === 'page').webSocketDebuggerUrl };
}

function cdp(url) {
  const ws = new WebSocket(url);
  let id = 0;
  const pending = new Map();
  const waiters = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) {
      waiters.filter((w) => w.method === msg.method).forEach((w) => { w.resolve(msg.params); waiters.splice(waiters.indexOf(w), 1); });
    }
  };
  return new Promise((ready) => {
    ws.onopen = () => ready({
      send: (method, params = {}) => new Promise((resolve, reject) => {
        pending.set(++id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      }),
      once: (method) => new Promise((resolve) => waiters.push({ method, resolve })),
      close: () => ws.close(),
    });
  });
}

async function main() {
  const server = await startServer();
  const browser = await launch();
  const c = await cdp(browser.ws);
  await c.send('Page.enable');
  await c.send('Runtime.enable');
  const evaluate = async (expr) => (await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result.value;
  const go = async (url) => { const loaded = c.once('Page.loadEventFired'); await c.send('Page.navigate', { url }); await loaded; };
  const viewport = (width, height) => c.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 });
  const shot = async (name, { width = 1360, full = true, maxHeight = 2600 } = {}) => {
    await viewport(width, 900);
    await sleep(500);
    const h = full ? Math.min(maxHeight, await evaluate('document.documentElement.scrollHeight')) : 900;
    await viewport(width, h);
    await sleep(900); // Chart.js redimensionne ses canevas
    const { data } = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
      clip: { x: 0, y: 0, width, height: h, scale: 1 } });
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'));
    console.log(`capture ${name} (${width}×${h})`);
  };
  const login = async (user) => {
    await c.send('Network.enable');
    await c.send('Network.clearBrowserCookies');
    await go(`${BASE}/connexion`);
    await evaluate(`localStorage.setItem('fusion-theme', 'light'); document.documentElement.dataset.theme = 'light'; 1`);
    await evaluate(`(() => { document.getElementById('identifiant').value = ${JSON.stringify(user)};
      document.getElementById('mot_de_passe').value = ${JSON.stringify(ACCOUNTS[user])};
      document.querySelector('form').submit(); return 1; })()`);
    await c.once('Page.loadEventFired');
    await sleep(1200);
  };
  const tab = async (section) => {
    await evaluate(`document.querySelector('[data-section="${section}"]').click(); 1`);
    await sleep(1500);
    await evaluate('window.scrollTo(0, 0); 1');
  };

  try {
    await viewport(1360, 900);
    await go(`${BASE}/connexion`);
    await evaluate(`localStorage.setItem('fusion-theme', 'light'); location.reload(); 1`);
    await sleep(800);
    await shot('connexion', { full: false });

    await login('admin');
    for (const s of ['synthese', 'clients', 'contrats', 'sinistres', 'finances', 'doublons', 'qualite']) {
      await tab(s);
      await shot(`admin-${s}`);
    }
    await tab('clients');
    await evaluate(`document.querySelector('[data-source="AP"]').click(); 1`);
    await sleep(1200);
    await shot('admin-clients-filtre-ap', { maxHeight: 1500 });
    await evaluate(`document.querySelector('[data-source="ALL"]').click(); 1`);
    await tab('contrats');
    await evaluate(`document.querySelector('.view-toggle').click(); 1`);
    await sleep(500);
    await shot('admin-contrats-vue-tableau', { maxHeight: 1100 });
    await evaluate(`localStorage.setItem('fusion-theme', 'dark'); location.reload(); 1`);
    await sleep(2500);
    await shot('admin-synthese-sombre', { maxHeight: 1400 });
    await evaluate(`localStorage.setItem('fusion-theme', 'light'); 1`);
    await viewport(390, 844);
    await go(`${BASE}/?vue=mobile#synthese`); // URL différente : vrai chargement (une ancre seule ne déclenche rien)
    await sleep(2000);
    await shot('mobile-synthese', { width: 390, maxHeight: 1800 });

    await login('analyste');
    await tab('doublons');
    await shot('utilisateur-doublons', { maxHeight: 1600 });
    const forbidden = await evaluate(`fetch('/api/gold/qualite').then(r => r.status)`);
    writeFileSync(join(OUT, 'utilisateur-acces-qualite.txt'), `GET /api/gold/qualite (rôle utilisateur) -> HTTP ${forbidden}\n`);
    console.log(`contrôle d'accès utilisateur → ${forbidden}`);

    const cov = join(root, 'htmlcov', 'index.html');
    if (existsSync(cov)) {
      await go(pathToFileURL(cov).href);
      await sleep(500);
      await shot('couverture', { width: 1200, maxHeight: 1500 });
    }
  } finally {
    c.close();
    browser.proc.kill();
    server.kill();
    await sleep(300);
    try { rmSync(browser.profile, { recursive: true, force: true }); } catch { /* profil verrouillé : ignoré */ }
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
