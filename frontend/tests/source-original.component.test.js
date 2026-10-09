// Focused V2 source component read/download contract; Main executes this case.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'
import { copyFor } from '../src/i18n/workbench.js'

Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
const pendingDigests = new Set(), actualDigest = webcrypto.subtle.digest.bind(webcrypto.subtle)
webcrypto.subtle.digest = (...args) => {
  const work = actualDigest(...args); pendingDigests.add(work)
  return work.finally(() => pendingDigests.delete(work))
}
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1:5173/research' })
for (const name of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node'])
  globalThis[name] = name === 'window' ? dom.window : dom.window[name]
const { createApp, nextTick, h, reactive } = await import('vue')
const path = '../src/components/workbench/SourceLibrary.vue'
const file = new URL(path, import.meta.url)
const { descriptor } = parse(readFileSync(file, 'utf8'), { filename: file.pathname })
const compiled = compileScript(descriptor, { id: path, inlineTemplate: true, genDefaultAs: '__component' })
let code = compiled.content.replace(/from (['"])vue\1/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
code = code.replace(/from (['"])(\.{1,2}\/[^'"]+)\1/g,
  (_match, _quote, relative) => `from ${JSON.stringify(new URL(relative, file).href)}`)
const Component = (await import(`data:text/javascript;base64,${Buffer.from(`${code}\nexport default __component`).toString('base64')}`)).default
const revision = '11111111-1111-4111-8111-111111111111'
const project = '22222222-2222-4222-8222-222222222222'
const bytes = new TextEncoder().encode('%PDF-1.7\noriginal\n%%EOF')
const encoded = Buffer.from(bytes).toString('base64')
const sha = createHash('sha256').update(bytes).digest('hex')
const source = { project_id: project, source_revision: revision, source_name: 'original.pdf',
  text_sha256: createHash('sha256').update('text').digest('hex'), byte_length: 4,
  codepoint_length: 4, recorded_at: '2026-10-07T00:00:00Z' }
const binary = { contract_version: 1, project_id: project, source_revision: revision,
  media_type: 'application/pdf', byte_length: bytes.length, sha256: sha }
const metadata = { schema_version: 2, binary_retained: true, graph_ingestion_executed: false,
  source, binary }
async function settle() { for (let i = 0; i < 20; i++) { await Promise.resolve(); await nextTick() } }
async function cryptoSettle() {
  const deadline = Date.now() + 2000
  await settle()
  while (pendingDigests.size) {
    const left = deadline - Date.now()
    assert.ok(left > 0, 'bounded WebCrypto validation did not complete')
    let timer
    try {
      await Promise.race([Promise.all([...pendingDigests]), new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error('bounded WebCrypto validation did not complete')), left)
      })])
    } finally { clearTimeout(timer) }
    await settle()
  }
}

const copy = copyFor('en').sources
function mountOriginal(methods) {
  const props = reactive({ connected: true, busy: false, resetVersion: 0, locale: 'en', methods })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(Component, props) }); app.mount(root)
  const button = label => [...root.querySelectorAll('button')].find(item => item.textContent.trim() === label)
  const input = (selector, value) => {
    const field = root.querySelector(selector)
    field.value = value; field.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
  }
  return { props, root, app, button, input, close: () => { app.unmount(); root.remove() } }
}

for (const mismatch of ['name', 'project']) {
  test(`uncertain original retention rejects changed ${mismatch} and keeps reconciliation identity`, async () => {
    let submitted, changed = true, submissions = 0
    const mounted = mountOriginal({
      list: async () => ({ sources: [source], has_more: false, window_limit: 20 }),
      retainOriginal: async payload => { submitted = { ...payload }; submissions++; throw { code: 'outcome_unknown' } },
      get: async () => ({ schema_version: 1, binary_retained: false, graph_ingestion_executed: false,
        source: { ...source, source_revision: submitted.source_revision, source_name: submitted.source_name }, text: 'text', passages: [] }),
      originalMetadata: async () => {
        const id = changed && mismatch === 'project' ? '33333333-3333-4333-8333-333333333333' : project
        return { ...metadata, source: { ...source, source_revision: submitted.source_revision,
          project_id: id, source_name: changed && mismatch === 'name' ? 'changed.pdf' : submitted.source_name },
        binary: { ...binary, source_revision: submitted.source_revision, project_id: id } }
      },
      originalRead: async () => { throw new Error('download must not be invoked by reconciliation') }
    })
    try {
      mounted.button(copy.load).click(); await settle()
      mounted.input('#source-name', 'original.pdf')
      const radio = mounted.root.querySelector('input[type="radio"][value="file"]')
      radio.checked = true; radio.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
      await settle()
      const fileInput = mounted.root.querySelector('#source-file')
      Object.defineProperty(fileInput, 'files', { configurable: true, value: [{ name: 'original.pdf',
        size: bytes.length, arrayBuffer: async () => bytes.slice().buffer }] })
      fileInput.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
      await settle()
      mounted.root.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      await cryptoSettle()
      assert.ok(mounted.button(copy.originalRetain))
      mounted.button(copy.originalRetain).click(); await settle()
      assert.equal(submissions, 1)
      assert.equal(mounted.root.querySelector('.attempt h3').textContent, copy.uncertain)
      mounted.button(copy.inspectAttempt).click(); await settle()
      assert.ok(mounted.root.querySelector('#source-inspector'))
      assert.equal(mounted.root.querySelector('.attempt h3').textContent, copy.uncertain)
      mounted.input('#source-original-revision', submitted.source_revision)
      mounted.button(copy.originalLookup).click(); await cryptoSettle()
      assert.equal(mounted.root.querySelector('.attempt h3').textContent, copy.uncertain)
      assert.ok(mounted.root.querySelector('.attempt').textContent.includes(submitted.source_revision))
      assert.ok(mounted.root.querySelector('.attempt').textContent.includes(submitted.input_sha256))
      assert.ok(mounted.root.querySelector('.attempt').textContent.includes(submitted.source_name))
      assert.equal(mounted.button(copy.originalDownload), undefined)
      assert.ok(mounted.root.querySelector('#source-feedback').textContent.includes(copy.invalidReply))
      changed = false
      mounted.button(copy.originalLookup).click(); await cryptoSettle()
      assert.equal(mounted.root.querySelector('.attempt h3').textContent, copy.revision)
      assert.ok(mounted.button(copy.originalDownload))
      assert.equal(submissions, 1)
    } finally { mounted.close() }
  })
}

test('synchronous download failure revokes its URL immediately without duplicate lifecycle cleanup', async () => {
  const revoked = [], downloads = []
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  const oldClick = dom.window.HTMLAnchorElement.prototype.click
  URL.createObjectURL = blob => { assert.equal(blob.size, bytes.length); return 'blob:click-failure' }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); throw new Error('fixture click failure') }
  const mounted = mountOriginal({ originalMetadata: async () => metadata,
    originalRead: async () => ({ ...metadata, content_base64: encoded }) })
  try {
    mounted.input('#source-original-revision', revision)
    mounted.button(copy.originalLookup).click(); await cryptoSettle()
    mounted.button(copy.originalDownload).click(); await cryptoSettle()
    assert.deepEqual(downloads, [`${revision}.pdf`])
    assert.deepEqual(revoked, ['blob:click-failure'])
    assert.ok(mounted.root.querySelector('#source-feedback').textContent.includes(copy.invalidReply))
    mounted.props.resetVersion++; await settle()
    mounted.close()
    assert.deepEqual(revoked, ['blob:click-failure'])
  } finally {
    if (mounted.root.isConnected) mounted.close()
    URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke
    dom.window.HTMLAnchorElement.prototype.click = oldClick
  }
})

for (const lifecycle of ['revision edit', 'disconnect', 'unmount']) {
  for (const phase of ['fetch', 'digest']) {
    test(`${lifecycle} aborts an original read delayed at ${phase} without publishing a download`, async () => {
      const calls = [], revoked = []
      const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
      const oldClick = dom.window.HTMLAnchorElement.prototype.click, trackedDigest = webcrypto.subtle.digest
      let release, signal, closed = false
      URL.createObjectURL = () => { calls.push('blob'); return 'blob:late' }
      URL.revokeObjectURL = url => revoked.push(url)
      dom.window.HTMLAnchorElement.prototype.click = () => calls.push('click')
      const result = { ...metadata, content_base64: encoded }
      const mounted = mountOriginal({ originalMetadata: async () => metadata,
        originalRead: (_identity, options) => {
          signal = options.signal
          return phase === 'fetch' ? new Promise(resolve => { release = () => resolve(result) }) : Promise.resolve(result)
        } })
      try {
        mounted.input('#source-original-revision', revision)
        mounted.button(copy.originalLookup).click(); await cryptoSettle()
        assert.ok(mounted.button(copy.originalDownload))
        if (phase === 'digest') webcrypto.subtle.digest = (...args) => new Promise((resolve, reject) => {
          release = () => Promise.resolve(trackedDigest(...args)).then(resolve, reject)
        })
        mounted.button(copy.originalDownload).click(); await settle()
        assert.equal(typeof release, 'function')
        assert.equal(signal.aborted, false)
        if (lifecycle === 'revision edit') mounted.input('#source-original-revision', '44444444-4444-4444-8444-444444444444')
        else if (lifecycle === 'disconnect') mounted.props.connected = false
        else { mounted.close(); closed = true }
        await settle()
        assert.equal(signal.aborted, true)
        webcrypto.subtle.digest = trackedDigest
        release(); await cryptoSettle()
        assert.deepEqual(calls, [])
        assert.deepEqual(revoked, [])
        if (!closed) {
          assert.equal(mounted.button(copy.originalDownload), undefined)
          assert.equal(mounted.root.textContent.includes(sha), false)
          assert.equal(mounted.root.querySelector('#source-original-revision').value,
            lifecycle === 'revision edit' ? '44444444-4444-4444-8444-444444444444' : '')
        }
      } finally {
        webcrypto.subtle.digest = trackedDigest
        if (!closed) mounted.close()
        URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke
        dom.window.HTMLAnchorElement.prototype.click = oldClick
      }
    })
  }
}

test('owned original lookup and exact download revoke URL on completion and reset', async () => {
  const calls = [], revoked = []
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  const oldClick = dom.window.HTMLAnchorElement.prototype.click
  URL.createObjectURL = blob => { assert.equal(blob.type, 'application/pdf'); return 'blob:fixture' }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () { calls.push(this.download) }
  const props = reactive({ connected: true, busy: false, resetVersion: 0, locale: 'en',
    methods: { originalMetadata: async (_identity, options) => {
        assert.ok(options.signal instanceof AbortSignal); assert.equal(options.signal.aborted, false)
        return metadata
      },
      originalRead: async (_identity, options) => {
        assert.ok(options.signal instanceof AbortSignal); assert.equal(options.signal.aborted, false)
        return { ...metadata, content_base64: encoded }
      } } })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(Component, props) }); app.mount(root)
  try {
    const input = root.querySelector('#source-original-revision')
    input.value = revision; input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
    await settle()
    const button = label => [...root.querySelectorAll('button')].find(item => item.textContent.includes(label))
    button('Check original PDF').click()
    await cryptoSettle()
    button('Download verified original').click()
    await cryptoSettle()
    assert.deepEqual(calls, [`${revision}.pdf`])
    assert.deepEqual(revoked, ['blob:fixture'])
    props.resetVersion++; await settle()
    assert.equal(root.querySelector('.source-window'), null)
  } finally {
    app.unmount(); root.remove(); URL.createObjectURL = oldCreate
    URL.revokeObjectURL = oldRevoke; dom.window.HTMLAnchorElement.prototype.click = oldClick
  }
})

test('digest delayed beyond reset cannot publish original metadata, bytes or Blob', async () => {
  const calls = [], revoked = []
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL
  const oldClick = dom.window.HTMLAnchorElement.prototype.click
  const trackedDigest = webcrypto.subtle.digest
  URL.createObjectURL = () => { calls.push('blob'); return 'blob:unexpected' }
  URL.revokeObjectURL = url => revoked.push(url)
  dom.window.HTMLAnchorElement.prototype.click = function () { calls.push('click') }
  let releaseDigest
  const props = reactive({ connected: true, busy: false, resetVersion: 0, locale: 'en',
    methods: { originalMetadata: async () => metadata,
      originalRead: async () => ({ ...metadata, content_base64: encoded }) } })
  const root = document.createElement('div'); document.body.append(root)
  const app = createApp({ setup: () => () => h(Component, props) }); app.mount(root)
  try {
    const revisionInput = root.querySelector('#source-original-revision')
    revisionInput.value = revision
    revisionInput.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
    const button = label => [...root.querySelectorAll('button')].find(item => item.textContent.includes(label))
    button('Check original PDF').click()
    await cryptoSettle()
    assert.ok(button('Download verified original'))
    webcrypto.subtle.digest = (...args) => new Promise((resolve, reject) => {
      releaseDigest = () => Promise.resolve(trackedDigest(...args)).then(resolve, reject)
    })
    button('Download verified original').click()
    await settle()
    assert.equal(typeof releaseDigest, 'function')
    props.resetVersion++
    await settle()
    assert.equal(root.querySelector('#source-original-revision').value, '')
    assert.equal(root.textContent.includes(sha), false)
    assert.equal(button('Download verified original'), undefined)
    webcrypto.subtle.digest = trackedDigest
    releaseDigest()
    await cryptoSettle()
    assert.deepEqual(calls, [])
    assert.deepEqual(revoked, [])
  } finally {
    webcrypto.subtle.digest = trackedDigest
    app.unmount(); root.remove(); URL.createObjectURL = oldCreate
    URL.revokeObjectURL = oldRevoke; dom.window.HTMLAnchorElement.prototype.click = oldClick
  }
})
