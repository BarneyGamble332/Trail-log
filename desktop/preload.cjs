// Brücke zwischen Oberfläche und Linux-Teil (Dateien speichern)
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  platform: process.platform,
  // where: 'downloads' → ~/Downloads/Trail Log, 'gallery' → ~/Bilder/Trail Log
  saveFile: (name, data, where) => ipcRenderer.invoke('saveFile', name, data, where),
  openPath: p => ipcRenderer.invoke('openPath', p),
  // Abgleich mit dem Handy: Adresse/Code anzeigen und Anfragen des Handys beantworten
  syncInfo: () => ipcRenderer.invoke('syncInfo'),
  syncStart: () => ipcRenderer.invoke('syncStart'),
  syncStop: () => ipcRenderer.invoke('syncStop'),
  onSyncStatus: fn => ipcRenderer.on('sync-status', (e, st) => fn(st)),
  onSync: fn => ipcRenderer.on('sync-req', async (e, { rid, op, data }) => {
    try { ipcRenderer.send('sync-res', { rid, ok: true, r: await fn(op, data) }); }
    catch (err) { ipcRenderer.send('sync-res', { rid, ok: false, r: String((err && err.message) || err) }); }
  }),
});
