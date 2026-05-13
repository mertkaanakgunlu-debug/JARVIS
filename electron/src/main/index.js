import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, screen } from 'electron'
import { join } from 'path'

// ── Constants ──────────────────────────────────────────────────────────────────
const JARVIS_API = process.env.JARVIS_API_URL || 'http://127.0.0.1:8000'
// electron-vite sets ELECTRON_RENDERER_URL only in dev server mode (npm run dev).
// In preview/production, this is undefined → load from built files.
const RENDERER_URL = process.env['ELECTRON_RENDERER_URL']

// ── Window handles ─────────────────────────────────────────────────────────────
let mainWindow = null
let widgetWindow = null
let tray = null

// ── Tray icon (16x16 nativeImage from inline base64 cyan "J") ─────────────────
function makeTrayIcon() {
  // Minimal 16x16 PNG: cyan "J" on black — encoded as base64
  // Generated via: python -c "from PIL import Image,ImageDraw; ..."
  // For portability we create it programmatically via nativeImage.createFromDataURL
  const size = 16
  // Build a simple icon: solid cyan circle
  const icon = nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAAlwSFlzAAAAdgAAAHYBTnsmCAAAABl0RVh0U29mdHdhcmUAd3d3Lmlua3NjYXBlLm9yZ5vuPBoAAAC5SURBVDiNpZMxDoMwDEWfK6oMHIA7cAuOwMARuAEbN2DlAlWqkCp1gAwZKmX4HRIkQoFi+UmWbL/v2LFdAMDMxMxfkqR1Xf9prb8AsACIiKo6AVidc4cQwgEAoqoWIrIBOOfcHkJYAaCI7DnntpxzexFJADbv/c3MrCml9FJKKcdYSimllBIAkCRJkiQJAADQWmuttdZaK0IIIYQQQgghhBBCCCGEEEIIIYQQQghf8AMCAgICAgICAgICAgICAgICAgIC8AMnHRNMkgAAAABJRU5ErkJggg=='
  )
  return icon
}

// ── Create main HUD window ─────────────────────────────────────────────────────
function createMainWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize

  mainWindow = new BrowserWindow({
    width: Math.min(1600, width),
    height: Math.min(960, height),
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    titleBarStyle: 'hidden',
    vibrancy: 'ultra-dark',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (RENDERER_URL) {
    mainWindow.loadURL(RENDERER_URL)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
    // DevTools only auto-opens in dev mode (RENDERER_URL branch)
    // In preview/production, use: tray → Open DevTools (HUD)
  }

  mainWindow.on('close', (e) => {
    // Hide instead of close; only actually close on quit
    e.preventDefault()
    mainWindow.hide()
  })

  mainWindow.webContents.on('did-fail-load', (_, code, desc, url) => {
    console.error(`[jarvis:main] renderer failed to load: ${code} ${desc} — ${url}`)
  })

  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow.webContents.send('config', { apiUrl: JARVIS_API })
    mainWindow.show()
  })
}

// ── Create floating orb widget ─────────────────────────────────────────────────
function createWidgetWindow() {
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize

  widgetWindow = new BrowserWindow({
    width: 220,
    height: 220,
    x: sw - 240,
    y: sh - 240,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (RENDERER_URL) {
    widgetWindow.loadURL(RENDERER_URL + '?mode=widget')
  } else {
    widgetWindow.loadFile(join(__dirname, '../renderer/index.html'), { query: { mode: 'widget' } })
  }

  widgetWindow.on('close', (e) => {
    e.preventDefault()
    widgetWindow.hide()
  })

  // Double-click widget → open main window
  widgetWindow.webContents.on('did-finish-load', () => {
    widgetWindow.webContents.send('config', { apiUrl: JARVIS_API })
  })
}

// ── System tray ────────────────────────────────────────────────────────────────
function createTray() {
  tray = new Tray(makeTrayIcon())
  tray.setToolTip('J.A.R.V.I.S.')

  const buildMenu = () => Menu.buildFromTemplate([
    { label: 'J.A.R.V.I.S. HUD', enabled: false },
    { type: 'separator' },
    {
      label: mainWindow?.isVisible() ? 'Hide HUD' : 'Show HUD',
      click: toggleMain,
    },
    {
      label: widgetWindow?.isVisible() ? 'Hide Widget' : 'Show Widget',
      click: toggleWidget,
    },
    { type: 'separator' },
    { label: 'Open DevTools (HUD)', click: () => mainWindow?.webContents.openDevTools({ mode: 'detach' }) },
    { type: 'separator' },
    { label: 'Quit JARVIS', click: () => { app.quit() } },
  ])

  tray.on('click', () => toggleMain())
  tray.on('right-click', () => {
    tray.setContextMenu(buildMenu())
    tray.popUpContextMenu()
  })
}

function toggleMain() {
  if (!mainWindow) return
  if (mainWindow.isVisible()) mainWindow.hide()
  else { mainWindow.show(); mainWindow.focus() }
}

function toggleWidget() {
  if (!widgetWindow) return
  if (widgetWindow.isVisible()) widgetWindow.hide()
  else widgetWindow.show()
}

// ── IPC handlers ───────────────────────────────────────────────────────────────
ipcMain.on('show-hud', () => { mainWindow?.show(); mainWindow?.focus() })
ipcMain.on('hide-hud', () => mainWindow?.hide())
ipcMain.on('show-widget', () => widgetWindow?.show())
ipcMain.on('hide-widget', () => widgetWindow?.hide())
ipcMain.on('open-hud-from-widget', () => { mainWindow?.show(); mainWindow?.focus() })

// Relay JARVIS state from any renderer to the other
ipcMain.on('jarvis-state', (_, state) => {
  // When JARVIS starts listening → show widget
  if (state === 'listening' || state === 'speaking') {
    widgetWindow?.show()
  }
  // Forward to both windows
  mainWindow?.webContents.send('jarvis-state', state)
  widgetWindow?.webContents.send('jarvis-state', state)
})

// ── App lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  createMainWindow()
  createWidgetWindow()
  createTray()

  // Show widget on first launch as a teaser
  setTimeout(() => widgetWindow?.show(), 1000)
})

app.on('before-quit', () => {
  // Allow windows to actually close on quit
  if (mainWindow) mainWindow.removeAllListeners('close')
  if (widgetWindow) widgetWindow.removeAllListeners('close')
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
