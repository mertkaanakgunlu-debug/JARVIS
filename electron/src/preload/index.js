import { contextBridge, ipcRenderer } from 'electron'

// Expose safe IPC bridge to renderer
contextBridge.exposeInMainWorld('jarvis', {
  // Send events to main process
  showHud:         () => ipcRenderer.send('show-hud'),
  hideHud:         () => ipcRenderer.send('hide-hud'),
  showWidget:      () => ipcRenderer.send('show-widget'),
  hideWidget:      () => ipcRenderer.send('hide-widget'),
  openHudFromWidget: () => ipcRenderer.send('open-hud-from-widget'),
  sendState:       (state) => ipcRenderer.send('jarvis-state', state),

  // Receive events from main process
  onConfig:        (cb) => ipcRenderer.on('config', (_, v) => cb(v)),
  onState:         (cb) => ipcRenderer.on('jarvis-state', (_, v) => cb(v)),

  // Cleanup
  removeAllListeners: (ch) => ipcRenderer.removeAllListeners(ch),
})
