// Runs within the existing ordinary restart stage on its owned protected host.
export async function inspectDossierSupport({ page, graph, sourceRoute, exchange, enabled, requireTrue, sha }) {
  const edge = graph.edges.find(item => typeof item.fact === 'string' && item.fact.trim())
  requireTrue(edge)
  const query = Array.from(edge.fact.trim()).slice(0, 180).join('')
  const form = page.locator('.query-section form')
  await form.locator('input[value="dossier"]').check()
  await form.locator('#dossier-title').fill('Retained evidence support')
  await form.locator('#heading-0').fill('Cited claims')
  await form.locator('#query-0').fill(query)
  await form.locator('.advanced-button').click()
  await form.locator('#top-k').fill('4')
  const dossierRoute = '/api/graph/dossier/' + graph.graph_id
  const observed = await exchange(page, dossierRoute, 'POST', () => form.locator('button[type="submit"]').click(), 263168)
  const dossier = observed.value
  requireTrue(observed.request.display_graph_ids.length === 1 && observed.request.display_graph_ids[0] === graph.graph_id &&
    observed.request.sections.length === 1 && observed.request.sections[0].query === query &&
    dossier.mode === 'model_free_evidence_dossier' && dossier.claim_support_status === 'not_reviewed' && dossier.semantic_judge_used === false)
  const reference = dossier.references.find(ref => ref.status === 'resolved' && ref.citation &&
    dossier.claims.some(claim => claim.key === ref.claim_key && claim.claim_class === 'source'))
  requireTrue(reference)
  const claim = dossier.claims.find(item => item.key === reference.claim_key)
  requireTrue(claim.claim_support_status === 'not_reviewed' && claim.reference_keys.includes(reference.key))
  const citation = reference.citation
  const row = page.locator('.evidence article.claim').filter({ has: page.getByText(claim.text, { exact: true }) })
  const citationControl = row.locator('.references button').filter({ hasText: citation.evidence_id }).first()
  const inspector = page.locator('#passage-inspector')
  const open = inspector.locator('.open-support-review')
  const verify = inspector.locator('.support-verify')
  const status = inspector.locator('.support-status')
  const source = sourceRoute('item', citation.source_revision)

  await enabled(citationControl)
  await citationControl.press('Enter')
  await open.waitFor({ state: 'visible', timeout: 15000 })
  await page.waitForFunction(() => document.activeElement?.id === 'passage-inspector', null, { timeout: 15000 })
  await open.click()
  await enabled(verify)
  requireTrue(await inspector.locator('.review-claim').textContent() === claim.text)
  requireTrue(await inspector.locator('.excerpt').textContent() === citation.excerpt)
  requireTrue(await inspector.locator('.support-record').count() === 0)

  const cap = 263168
  const originalRequest = page.waitForRequest(
    request => request.method() === 'GET' && new URL(request.url()).pathname === source,
    { timeout: 15000 },
  )
  const [read, retainedRequest] = await Promise.all([
    exchange(page, source, 'GET', () => verify.click(), cap),
    originalRequest,
  ])
  const value = read.value
  const passage = value.passages.find(item => item.evidence_id === citation.evidence_id)
  const excerpt = Array.from(value.text).slice(citation.start, citation.end).join('')
  requireTrue(read.request === null && value.source.project_id === citation.project_id &&
    value.source.source_revision === citation.source_revision && value.source.text_sha256 === citation.source_sha256 &&
    sha(value.text) === citation.source_sha256 && passage && passage.start === citation.start && passage.end === citation.end &&
    passage.excerpt_sha256 === citation.excerpt_sha256 && sha(excerpt) === citation.excerpt_sha256 && excerpt === citation.excerpt)
  await status.filter({ hasText: 'Exact retained passage verified.' }).waitFor({ state: 'visible', timeout: 15000 })
  await inspector.locator('input[value="insufficient"]').check()
  await inspector.locator('.support-record').press('Enter')
  await status.filter({ hasText: 'Session judgment · Insufficient evidence' }).waitFor({ state: 'visible', timeout: 15000 })

  const connectionStatus = page.locator('.connection .status')
  const connectedText = await connectionStatus.textContent()
  const retainedUrl = retainedRequest.url()
  const initial = status.filter({ hasText: /^\s*Not reviewed\s*$/ })
  let markHeld, release
  const held = new Promise(resolve => { markHeld = resolve })
  const released = new Promise(resolve => { release = resolve })
  let mismatch = false, pending
  async function fulfillRetained(route, changeTimestamp) {
    const response = await route.fetch({ maxRedirects: 0, timeout: 5000 })
    try {
      const headers = response.headers()
      const length = Number(headers['content-length'])
      requireTrue(response.status() === 200 && /^\d+$/.test(headers['content-length']) &&
        length > 0 && length <= cap && !headers['content-encoding'])
      if (!changeTimestamp) {
        markHeld(true)
        await released
        await route.fulfill({ response })
        return
      }
      const envelope = await response.json()
      const stamp = envelope.data.source.recorded_at
      requireTrue(stamp === value.source.recorded_at)
      const altered = stamp.replace(/(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/, (_, fraction, zone) => {
        const digits = (fraction?.slice(1) || '').padEnd(6, '0')
        return '.' + digits.slice(0, 3) + (digits[3] === '9' ? '0' : '9') + digits.slice(4) + zone
      })
      requireTrue(altered !== stamp && Date.parse(altered) === Date.parse(stamp))
      envelope.data.source.recorded_at = altered
      const body = JSON.stringify(envelope)
      const size = Buffer.byteLength(body)
      requireTrue(size <= cap)
      await route.fulfill({ response, body, headers: { ...headers, 'content-length': String(size) } })
    } finally {
      await response.dispose()
    }
  }
  const intercept = route => {
    if (route.request().method() !== 'GET') return route.continue()
    pending = fulfillRetained(route, mismatch).catch(async error => {
      markHeld(false)
      await route.abort()
      throw error
    })
    return pending.catch(() => {})
  }
  await page.route(retainedUrl, intercept)
  try {
    // The protected reply stays at the browser route while a replacement inspector opens.
    const late = await exchange(page, source, 'GET', async () => {
      await verify.click()
      requireTrue(await held)
      await inspector.press('Escape')
      await verify.waitFor({ state: 'hidden', timeout: 15000 })
      requireTrue(await citationControl.evaluate(node => document.activeElement === node))
      await citationControl.press('Enter')
      await enabled(open)
      await open.click()
      await initial.waitFor({ state: 'visible', timeout: 15000 })
      release()
    }, cap)
    await pending
    requireTrue(late.value.source.source_revision === citation.source_revision &&
      late.value.source.recorded_at === value.source.recorded_at)
    await enabled(page.locator('.graph-refresh'))
    requireTrue(await connectionStatus.textContent() === connectedText &&
      await initial.count() === 1 && await inspector.locator('form').count() === 0)

    // The next protected reply differs only beyond millisecond precision.
    mismatch = true
    const altered = await exchange(page, source, 'GET', () => verify.click(), cap)
    await pending
    requireTrue(altered.value.source.recorded_at !== value.source.recorded_at &&
      Date.parse(altered.value.source.recorded_at) === Date.parse(value.source.recorded_at))
    await status.filter({ hasText: 'The retained passage could not be verified.' }).waitFor({ state: 'visible', timeout: 15000 })
    requireTrue(await inspector.locator('form').count() === 0)
  } finally {
    markHeld(false)
    release()
    try { await page.unroute(retainedUrl, intercept) }
    finally { await pending }
  }
  await inspector.press('Escape')
}
