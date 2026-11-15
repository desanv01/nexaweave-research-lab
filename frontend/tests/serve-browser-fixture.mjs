// Main-only local browser qualification; no inherited proxy or dotenv loading.
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { mkdtemp, rmdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../', import.meta.url))
// Vite otherwise treats a closed noninteractive stdin as a parent shutdown.
// This affects this fixture process only; no account or project setting changes.
process.env.CI = 'true'
process.env.BROWSER = 'none'
delete process.env.BROWSER_ARGS

let environmentDirectory
let server
let closing
let input = ''
let stopped
const lifetime = new Promise(resolve => { stopped = resolve })
const onSignal = () => { void close() }
const onInput = (value) => {
  input += value
  if (Buffer.byteLength(input, 'utf8') > 7200) { void close(true); return }
  if (input.trim() === 'stop') void close()
}
const onEnd = () => { void close(input.trim() !== '' && input.trim() !== 'stop') }
const onInputError = () => { void close(true) }

const close = (failed = false) => {
  if (closing) return closing
  closing = (async () => {
    process.off('SIGINT', onSignal)
    process.off('SIGTERM', onSignal)
    process.stdin.off('data', onInput)
    process.stdin.off('end', onEnd)
    process.stdin.off('error', onInputError)
    let cleanupFailed = false
    try {
      process.stdin.pause()
      process.stdin.destroy()
    } catch {
      cleanupFailed = true
    }
    if (server) {
      try {
        const httpServer = server.httpServer
        const shutdown = server.close()
        // Vite's full close still owns/awaits HMR websockets, watchers, plugins
        // and optimizer resources. This Node API closes only this fixture's
        // HTTP connections; it does not close upgraded websocket connections.
        try { httpServer?.closeAllConnections() } catch { cleanupFailed = true }
        await shutdown
      } catch {
        cleanupFailed = true
      }
    }
    if (environmentDirectory) {
      try {
        // Only the empty directory created by this fixture may be removed.
        await rmdir(environmentDirectory)
      } catch {
        cleanupFailed = true
      }
    }
    if (cleanupFailed) console.error('Offline browser fixture cleanup failed')
    if (failed || cleanupFailed) process.exitCode = 1
    stopped()
  })()
  return closing
}

try {
  environmentDirectory = await mkdtemp(path.join(tmpdir(), 'nexaweave-browser-env-'))
  server = await createServer({
    configFile: false, root, envDir: environmentDirectory, plugins: [vue()],
    resolve: { alias: { '@': path.join(root, 'src'), '@locales': path.join(root, '../locales') } },
    optimizeDeps: { entries: ['tests/browser-rendering.html'] },
    server: { host: '127.0.0.1', port: 4317, strictPort: true, open: false },
  })
  await server.listen()
  process.on('SIGINT', onSignal)
  process.on('SIGTERM', onSignal)
  process.stdin.setEncoding('utf8')
  process.stdin.on('end', onEnd)
  process.stdin.on('error', onInputError)
  process.stdin.on('data', onInput)
  console.log('NexaWeave offline browser fixture: http://127.0.0.1:4317/tests/browser-rendering.html')
  // Keep module lifetime bound to complete cleanup, including signal/EOF stops.
  await lifetime
  await closing
} catch {
  console.error('Offline browser fixture failed to start; check Vite setup and port 4317')
  await close(true)
}
