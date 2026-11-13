// Trail Log für Linux: Fenster und Speichern von Dateien.
// Die Oberfläche ist dieselbe index.html wie in der Android-App.
import { app, BrowserWindow, ipcMain, shell, session } from 'electron';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
app.setName('Trail Log');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('enable-gpu-rasterization');

if (!app.requestSingleInstanceLock()) app.quit();

const dataDir = () => app.getPath('userData');
const readJSON = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(dataDir(), f), 'utf8')); } catch { return d; } };
const writeJSON = (f, v) => { try { fs.writeFileSync(path.join(dataDir(), f), JSON.stringify(v)); } catch { /* egal */ } };

let win;
function createWindow() {
  const st = readJSON('window.json', {});
  win = new BrowserWindow({
    width: st.w || 1280, height: st.h || 860, x: st.x, y: st.y,
    minWidth: 380, minHeight: 600,
    backgroundColor: '#f3efe8', title: 'Trail Log', autoHideMenuBar: true,
    icon: path.join(__dirname, 'build', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      // Die Seite ist lokal; abgeschaltet, damit Karten, Gipfel und Wetter anderer Seiten geladen werden können
      webSecurity: false,
    },
  });
  if (st.max) win.maximize();
  win.loadFile(path.join(__dirname, 'app', 'index.html'));
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith('file:')) { e.preventDefault(); shell.openExternal(url); } });
  win.on('close', () => { const b = win.getNormalBounds(); writeJSON('window.json', { w: b.width, h: b.height, x: b.x, y: b.y, max: win.isMaximized() }); });
}

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.whenReady().then(() => {
  // Kartenserver wollen wissen, woher die Anfrage kommt (bei einer lokalen Datei fehlt das sonst)
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: ['https://*/*'] }, (d, cb) => {
    if (!d.requestHeaders.Referer) d.requestHeaders.Referer = 'https://barneygamble332.github.io/Trail-log/';
    cb({ requestHeaders: d.requestHeaders });
  });
  createWindow();
});
app.on('window-all-closed', () => app.quit());

/* Speichern ohne Nachfrage, wie am Handy: Downloads › Trail Log bzw. Bilder › Trail Log */
ipcMain.handle('saveFile', async (e, name, data, where) => {
  const base = app.getPath(where === 'gallery' ? 'pictures' : 'downloads');
  const dir = path.join(base, 'Trail Log');
  fs.mkdirSync(dir, { recursive: true });
  const safe = String(name).replace(/[\/\\\0]/g, '_') || 'datei';
  const ext = path.extname(safe), stem = safe.slice(0, safe.length - ext.length);
  let p = path.join(dir, safe), i = 2;
  while (fs.existsSync(p)) p = path.join(dir, `${stem} (${i++})${ext}`);
  fs.writeFileSync(p, Buffer.from(data));
  return p;
});
ipcMain.handle('openPath', (e, p) => (typeof p === 'string' ? shell.showItemInFolder(p) : null));

/* ---------- Abgleich mit dem Handy übers WLAN ----------
   Das Handy schickt POST /tl/list|get|put|done mit dem Code im Kopf "X-Code".
   Die Touren liegen in der Oberfläche (IndexedDB), deshalb wird jede Anfrage dorthin weitergereicht. */
const SYNC_PORT = 8787;
let rid = 0, fails = [];
const pending = new Map();
function syncCode() {
  const s = readJSON('settings.json', {});
  if (!/^\d{4}$/.test(s.code || '')) { s.code = String(crypto.randomInt(1000, 10000)); writeJSON('settings.json', s); }
  return s.code;
}
function ips() {
  return Object.values(os.networkInterfaces()).flat()
    .filter(i => i && i.family === 'IPv4' && !i.internal && !/^(172\.(1[7-9]|2\d|3[01])|169\.254)\./.test(i.address))
    .map(i => i.address);
}
function ask(op, data) {
  return new Promise((res, rej) => {
    if (!win) return rej(new Error('Fenster fehlt'));
    const id = ++rid; pending.set(id, { res, rej });
    win.webContents.send('sync-req', { rid: id, op, data });
    setTimeout(() => { if (pending.delete(id)) rej(new Error('Zeitüberschreitung')); }, 120000);
  });
}
ipcMain.on('sync-res', (e, { rid: id, ok, r }) => { const p = pending.get(id); if (!p) return; pending.delete(id); ok ? p.res(r) : p.rej(new Error(r)); });
ipcMain.handle('syncInfo', () => ({ ips: ips(), code: syncCode(), port: SYNC_PORT, open: server.listening, until: openUntil }));

/* Der PC nimmt nur Verbindungen an, nachdem am PC „Abgleich starten“ gedrückt wurde – 2 Minuten lang.
   Läuft gerade eine Übertragung, verlängert jede Anfrage um 30 s; nach „fertig“ schließt er gleich. */
const OPEN_MS = 120000;
let openUntil = 0, closeTimer = null;
function status(err) { if (win && !win.isDestroyed()) win.webContents.send('sync-status', { open: server.listening, until: openUntil, err: err || '' }); }
function armClose() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => syncStop(), Math.max(0, openUntil - Date.now()));
}
function syncStop() {
  clearTimeout(closeTimer); openUntil = 0;
  if (server.listening) { server.close(() => status()); server.closeAllConnections(); } else status();
}
ipcMain.handle('syncStart', () => new Promise(resolve => {
  openUntil = Date.now() + OPEN_MS; armClose();
  if (server.listening) { status(); return resolve({ ok: true, until: openUntil }); }
  const onErr = err => { server.off('listening', onOk); openUntil = 0; clearTimeout(closeTimer); status(err.code === 'EADDRINUSE' ? 'Port ' + SYNC_PORT + ' ist belegt.' : err.message); resolve({ ok: false, err: err.message }); };
  const onOk = () => { server.off('error', onErr); status(); resolve({ ok: true, until: openUntil }); };
  server.once('error', onErr); server.once('listening', onOk);
  server.listen(SYNC_PORT, '0.0.0.0');
}));
ipcMain.handle('syncStop', () => { syncStop(); return true; });

const server = http.createServer((req, res) => {
  const send = (c, o) => { res.writeHead(c, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(o)); };
  const m = /^\/tl\/(list|get|put|done)$/.exec(req.url || '');
  if (req.method !== 'POST' || !m) return send(404, { error: 'unbekannt' });
  const now = Date.now(); fails = fails.filter(t => now - t < 60000);
  if (fails.length >= 10) return send(429, { error: 'zu viele Versuche, kurz warten' });
  if (req.headers['x-code'] !== syncCode()) { fails.push(now); return send(403, { error: 'code' }); }
  const chunks = []; let size = 0;
  req.on('data', c => { size += c.length; if (size > 200e6) req.destroy(); else chunks.push(c); });
  req.on('end', async () => {
    let data = {};
    try { data = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}; } catch { return send(400, { error: 'json' }); }
    openUntil = Math.max(openUntil, Date.now() + 30000); armClose();
    try { send(200, await ask(m[1], data)); } catch (err) { send(500, { error: String(err.message || err) }); }
    if (m[1] === 'done') { openUntil = Date.now() + 1500; armClose(); }
  });
});
server.on('error', err => console.warn('Abgleich:', err.message));
app.on('will-quit', () => { if (server.listening) { server.close(); server.closeAllConnections(); } });
