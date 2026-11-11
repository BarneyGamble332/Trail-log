// Trail Log für Linux: Fenster und Speichern von Dateien.
// Die Oberfläche ist dieselbe index.html wie in der Android-App.
import { app, BrowserWindow, ipcMain, shell, session } from 'electron';
import fs from 'node:fs';
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
