import { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, screen, session } from 'electron'
import { join } from 'path'
import { spawn } from 'child_process'
import { existsSync, readFileSync } from 'fs'
import http from 'http'

// ── Constants ──────────────────────────────────────────────────────────────────
const JARVIS_API = process.env.JARVIS_API_URL || 'http://127.0.0.1:8000'
// electron-vite sets ELECTRON_RENDERER_URL only in dev server mode (npm run dev).
// In preview/production, this is undefined → load from built files.
const RENDERER_URL = process.env['ELECTRON_RENDERER_URL']

function jarvisRoot() {
  // JARVIS_ROOT env override (useful for packaged builds)
  if (process.env.JARVIS_ROOT) return process.env.JARVIS_ROOT
  // app.getAppPath() → .../Jarvis/electron  →  parent = repo root
  return join(app.getAppPath(), '..')
}

// Faz 3: the renderer needs JARVIS_API_KEY to authenticate its /ws connection
// (?token=) now that the socket can carry live mic audio and synthesized
// speech, not just read-only telemetry — a bigger stakes jump than before.
// The Python backend reads this from the same .env file via its own
// load_dotenv() call; Electron's own process.env normally won't have it set
// (it isn't inherited from anywhere), so read the .env file directly rather
// than adding a new npm dependency for one value.
function readEnvValue(key) {
  try {
    const envPath = join(jarvisRoot(), '.env')
    if (!existsSync(envPath)) return ''
    const content = readFileSync(envPath, 'utf-8')
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eq = trimmed.indexOf('=')
      if (eq === -1) continue
      if (trimmed.slice(0, eq).trim() !== key) continue
      let value = trimmed.slice(eq + 1).trim()
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1)
      }
      return value
    }
  } catch (_) {
    // Missing/unreadable .env just means no key -- same as auth being disabled
    // server-side, not a crash-worthy condition.
  }
  return ''
}

const JARVIS_API_KEY = process.env.JARVIS_API_KEY || readEnvValue('JARVIS_API_KEY')

// ── Python backend management ──────────────────────────────────────────────────
let pythonProcess = null

function isPortInUse(port) {
  return new Promise(resolve => {
    const req = http.get(`http://127.0.0.1:${port}/health`, res => {
      resolve(res.statusCode === 200)
    })
    req.on('error', () => resolve(false))
    req.end()
  })
}

async function startPythonBackend() {
  const port = 8000
  if (await isPortInUse(port)) {
    console.log('[jarvis:main] Backend already running on port', port, '— skipping spawn')
    return
  }
  const root = jarvisRoot()
  const pyExe = join(root, '.venv', 'Scripts', 'python.exe')
  if (!existsSync(pyExe)) {
    console.warn('[jarvis:main] Python venv not found at', pyExe, '— skipping spawn')
    return
  }
  console.log('[jarvis:main] Starting Python backend…')
  pythonProcess = spawn(pyExe, ['-m', 'jarvis', '--api', '--monitor', '--wakeword'], {
    cwd: root,
    windowsHide: true,
    env: { ...process.env },
  })
  pythonProcess.stdout.on('data', d => process.stdout.write('[py] ' + d))
  pythonProcess.stderr.on('data', d => process.stderr.write('[py] ' + d))
  pythonProcess.on('exit', code => console.log('[jarvis:main] Python exited with code', code))
}

function stopPythonBackend() {
  if (!pythonProcess || pythonProcess.killed) return
  console.log('[jarvis:main] Shutting down Python backend…')
  pythonProcess.kill('SIGTERM')
  pythonProcess = null
}

function waitForBackend(maxRetries = 40, intervalMs = 750) {
  return new Promise(resolve => {
    let tries = 0
    const check = () => {
      const req = http.get('http://127.0.0.1:8000/health', res => {
        if (res.statusCode === 200) { resolve(true) }
        else retry()
      })
      req.on('error', retry)
      req.end()
    }
    const retry = () => { if (++tries >= maxRetries) resolve(false); else setTimeout(check, intervalMs) }
    check()
  })
}

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
    mainWindow.webContents.send('config', { apiUrl: JARVIS_API, apiKey: JARVIS_API_KEY })
    // Window is shown by app.whenReady after backend is confirmed up
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
    widgetWindow.webContents.send('config', { apiUrl: JARVIS_API, apiKey: JARVIS_API_KEY })
  })
}

// ── System tray ────────────────────────────────────────────────────────────────
function createTray() {
  tray = new Tray(makeTrayIcon())
  tray.setToolTip('J.A.R.V.I.S.')

  const buildMenu = () => {
    const autoStart = app.getLoginItemSettings().openAtLogin
    return Menu.buildFromTemplate([
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
      {
        label: autoStart ? 'Disable Auto-start' : 'Enable Auto-start',
        click: () => {
          app.setLoginItemSettings({ openAtLogin: !autoStart, openAsHidden: true })
        },
      },
      { type: 'separator' },
      { label: 'Open DevTools (HUD)', click: () => mainWindow?.webContents.openDevTools({ mode: 'detach' }) },
      { type: 'separator' },
      { label: 'Quit JARVIS', click: () => { app.quit() } },
    ])
  }

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
  if (state === 'idle') {
    // Hide widget when idle — it should only appear while processing
    widgetWindow?.hide()
  } else if (!mainWindow?.isVisible()) {
    // Show widget only when HUD is closed; if HUD is open, no need for widget
    widgetWindow?.show()
  }
  mainWindow?.webContents.send('jarvis-state', state)
  widgetWindow?.webContents.send('jarvis-state', state)
})

// ── App lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  // Auto-start with Windows (silent — no window on login)
  app.setLoginItemSettings({ openAtLogin: true, openAsHidden: true })

  // Faz 3: explicit permission handler for the remote-audio session's
  // getUserMedia() call, rather than relying on Electron's version-dependent
  // default behavior for media requests.
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
    callback(permission === 'media')
  })

  createTray()          // tray icon visible immediately
  createMainWindow()    // hidden — user opens manually or via JARVIS activity
  createWidgetWindow()  // hidden

  startPythonBackend()

  tray.setToolTip('J.A.R.V.I.S. — starting…')
  const ready = await waitForBackend()
  tray.setToolTip(ready ? 'J.A.R.V.I.S.' : 'J.A.R.V.I.S. (backend unreachable)')
  console.log(ready ? '[jarvis:main] Backend ready — tray only' : '[jarvis:main] Backend timeout')
  // HUD stays hidden; user opens via tray click or JARVIS state change
})

app.on('before-quit', () => {
  stopPythonBackend()
  if (mainWindow) mainWindow.removeAllListeners('close')
  if (widgetWindow) widgetWindow.removeAllListeners('close')
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
