// Restart-only acceptance: recover one exact retained source revision through
// the authenticated inspector before any catalog load, then preserve the
// existing old-PDF and origin-denial assertions.
const readText = (locator, limit) => locator.evaluate((node, cap) => {
  const value = node.textContent
  return value !== null && value.length <= cap ? value : null
}, limit)

async function recoverRetainedNotes({ page, notes, readItem, enabled, sha, requireTrue, setPhase }) {
  setPhase('source-inspect')
  requireTrue(notes && notes.byte_length > 0 && notes.byte_length <= 1024)
  requireTrue(await page.locator('.source-window').count() === 0)
  const revision = page.locator('#source-inspect-revision')
  const submit = page.locator('#source-revision-inspect')
  await enabled(submit)
  await revision.fill(notes.source_revision)

  const { request, value } = await readItem(notes.source_revision, () => revision.press('Enter'))
  requireTrue(request === null && value.schema_version === 1 && value.binary_retained === false
    && value.graph_ingestion_executed === false && value.offset_unit === 'unicode_codepoint')
  const { source, text, passages } = value
  requireTrue(source.project_id === notes.project_id && source.source_revision === notes.source_revision
    && source.source_name === notes.source_name && source.text_sha256 === notes.text_sha256
    && source.byte_length === notes.byte_length && source.codepoint_length === notes.codepoint_length
    && typeof text === 'string' && Buffer.byteLength(text) === notes.byte_length && sha(text) === notes.text_sha256)
  const points = Array.from(text)
  requireTrue(points.length === notes.codepoint_length && Array.isArray(passages) && passages.length === 1)
  const [passage] = passages
  requireTrue(passage.start === 0 && passage.end === points.length && passage.page === null
    && passage.excerpt_sha256 === notes.text_sha256)

  const inspector = page.locator('#source-inspector')
  await inspector.waitFor({ state: 'visible', timeout: 15000 })
  await enabled(submit)
  requireTrue(await inspector.evaluate(node => node === document.activeElement))
  requireTrue(await readText(inspector.locator('h3'), 256) === notes.source_name)
  requireTrue(await readText(inspector.locator(':scope > .mono'), 36) === notes.source_revision)
  await inspector.locator('summary').click()
  requireTrue(await readText(inspector.locator('.exact-text'), 1024) === text)

  const passageButton = inspector.locator('ol button').filter({ hasText: passage.evidence_id })
  await passageButton.click()
  const excerpt = inspector.locator('#source-excerpt')
  await excerpt.waitFor({ state: 'visible', timeout: 15000 })
  requireTrue(await readText(excerpt.locator('.excerpt-text'), 1024) === text)
  requireTrue(await readText(excerpt.locator('dd').last(), 64) === passage.excerpt_sha256)
  await excerpt.press('Escape')
  await excerpt.waitFor({ state: 'hidden', timeout: 15000 })
  await inspector.press('Escape')
  await inspector.waitFor({ state: 'hidden', timeout: 15000 })
  await page.waitForFunction(() => document.activeElement?.id === 'source-revision-inspect', null, { timeout: 15000 })
  requireTrue(await page.locator('.source-window').count() === 0)
  return { source_revision: source.source_revision, text_sha256: source.text_sha256,
    evidence_id: passage.evidence_id, excerpt_sha256: passage.excerpt_sha256 }
}

export async function runResearchBrowserRestart({
  page, context, pdfRevision, notes, connect, readPdf, readItem, observe, enabled, sha, requireTrue, setPhase,
  afterConnect = null, afterRestart = null,
}) {
  await page.locator('.connection-form button[type=button]').last().click()
  await enabled(page.locator('.connection-form button[type=submit]'))
  // Exercise the wrong credential through the form, then reconnect.
  await connect(page, 401)
  const { value: graph } = await connect(page)
  // The recovery must run while no source catalog window exists.
  const recoveredSource = await recoverRetainedNotes({ page, notes, readItem, enabled, sha, requireTrue, setPhase })
  if (afterConnect) { setPhase('graph-connect'); await afterConnect(graph) }
  const original = await readPdf()
  // Use a second ordinary frontend origin to observe actual server refusal.
  const denied = await context.newPage()
  observe(denied)
  try {
    setPhase('page-load')
    await denied.goto('http://localhost:4317/research', { waitUntil: 'domcontentloaded' })
    await denied.locator('#workbench-language').selectOption('en')
    await connect(denied, 403)
  } finally { await denied.close() }
  requireTrue(await page.locator('#bearer-token').inputValue() === '')
  setPhase('protocol')
  if (afterRestart) await afterRestart(graph)
  return {
    reference: { source_revision: pdfRevision },
    proof: { pdf: original, auth_denied: true, origin_denied: true, token_cleared: true, recovered_source: recoveredSource },
  }
}
