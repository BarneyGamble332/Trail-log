// Brücke zwischen Oberfläche und Linux-Teil (Dateien speichern)
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktop', {
  platform: process.platform,
  // where: 'downloads' → ~/Downloads/Trail Log, 'gallery' → ~/Bilder/Trail Log
  saveFile: (name, data, where) => ipcRenderer.invoke('saveFile', name, data, where),
  openPath: p => ipcRenderer.invoke('openPath', p),
});
