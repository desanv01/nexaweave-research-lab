import test from 'node:test'
import assert from 'node:assert/strict'

export function registerTemporalClaimsTest({ dom, workbench, mount, settle, input, researchResult, graphResult, response }) {
  test('actual route displays only admitted claim timestamps across historical and current reads', async () => {
    const validAt = '2026-10-01T12:00:00Z'
    const created = '2026-10-01T09:00:00Z', valid = '2026-10-01T08:00:00Z'
    const invalid = '2026-10-02T00:00:00Z', expired = '2026-10-03T00:00:00Z'
    const laterRecordingCutoff = '2026-10-04T00:00:00Z', earlierRecordingCutoff = '2026-10-02T12:00:00Z'
    const known = researchResult(), concealed = researchResult(), current = researchResult()
    known.historical = concealed.historical = true
    Object.assign(known.source_claims[0], { fact: 'Earlier retained claim', created_at: created, valid_at: valid, invalid_at: invalid, expired_at: expired })
    Object.assign(concealed.source_claims[0], { fact: 'Earlier retained claim', created_at: created, valid_at: valid })
    current.source_claims[0].fact = 'Current retained claim'
    const previousFetch = globalThis.fetch, calls = []
    const replies = [graphResult, known, concealed, current]
    globalThis.fetch = async (url, options) => { calls.push({ url, options }); return response(replies[calls.length - 1]) }
    const mounted = mount(workbench.component)
    try {
      const timestamps = () => [...mounted.root.querySelectorAll('.claim-times time')].map(time => [time.getAttribute('datetime'), time.textContent])
      const labels = () => [...mounted.root.querySelectorAll('.claim-times dt')].map(label => label.textContent)
      const submit = () => mounted.root.querySelector('.query-section form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
      input(mounted.root, '#graph-id', 'graph_1'); input(mounted.root, '#bearer-token', 'test-token')
      mounted.root.querySelector('.connection-form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); await settle()
      mounted.root.querySelector('.advanced-button').click(); await settle()
      input(mounted.root, '#question', 'Earlier retained claim')
      input(mounted.root, '#valid-at', validAt); input(mounted.root, '#recorded-before', laterRecordingCutoff)
      submit(); await settle()
      assert.equal(calls[1].url, 'http://127.0.0.1:5001/api/graph/research/graph_1')
      assert.equal(JSON.parse(calls[1].options.body).valid_at, validAt)
      assert.equal(JSON.parse(calls[1].options.body).recorded_before, laterRecordingCutoff)
      assert.equal(mounted.root.querySelector('.historical-result').textContent, 'Historical view of retained evidence')
      assert.deepEqual(labels(), ['Recorded at', 'Valid from', 'Valid until', 'Invalidation recorded at'])
      assert.deepEqual(timestamps(), [[created, created], [valid, valid], [invalid, invalid], [expired, expired]])

      input(mounted.root, '#workbench-language', 'zh'); await settle()
      assert.deepEqual(labels().slice(-2), ['失效时间', '失效记录时间'])
      input(mounted.root, '#workbench-language', 'ms'); await settle()
      assert.deepEqual(labels().slice(-2), ['Sah sehingga', 'Masa ketidaksahan direkodkan'])
      assert.equal(calls.length, 2)

      input(mounted.root, '#recorded-before', earlierRecordingCutoff)
      submit(); await settle()
      assert.equal(JSON.parse(calls[2].options.body).recorded_before, earlierRecordingCutoff)
      assert.deepEqual(timestamps(), [[created, created], [valid, valid]])
      assert.equal(mounted.root.querySelector('.historical-result').textContent, 'Paparan sejarah bukti tersimpan')

      input(mounted.root, '#valid-at', ''); input(mounted.root, '#recorded-before', '')
      submit(); await settle()
      const payload = JSON.parse(calls[3].options.body)
      assert.equal(Object.hasOwn(payload, 'valid_at'), false)
      assert.equal(Object.hasOwn(payload, 'recorded_before'), false)
      assert.equal(mounted.root.querySelector('.claim-text').textContent, 'Current retained claim')
      assert.equal(mounted.root.querySelector('.claim-times, .historical-result'), null)
      assert.equal(calls.length, 4)
    } finally { mounted.cleanup(); globalThis.fetch = previousFetch }
  })
}
