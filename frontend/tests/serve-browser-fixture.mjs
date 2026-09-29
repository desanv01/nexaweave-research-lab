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

let environmentDirectory
let server
let closing
const onSignal = () => { void close() }
const onInput = (value) => { if (value.trim() === 'stop') void close() }

const close = (failed = false) => {
  if (closing) return closing
  closing = (async () => {
    process.off('SIGINT', onSignal)
    process.off('SIGTERM', onSignal)
    process.stdin.off('data', onInput)
    process.stdin.pause()
    let cleanupFailed = false
    if (server) {
      try {
        await server.close()
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
  })()
  return closing
}

try {
  environmentDirectory = await mkdtemp(path.join(tmpdir(), 'mirofish-browser-env-'))
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
  process.stdin.on('data', onInput)
  console.log('Offline browser fixture: http://127.0.0.1:4317/tests/browser-rendering.html')
} catch {
  console.error('Offline browser fixture failed to start; check Vite setup and port 4317')
  await close(true)
}
