import test from 'node:test'
import assert from 'node:assert/strict'
import { prepareDossierExport, DOSSIER_EXPORT_LIMIT } from '../src/api/dossierExport.js'
const uuid = '11111111-1111-1111-1111-111111111111'
const scope = { schema_version: 1, workspace_id: uuid, project_id: uuid, graph_id: uuid, run_id: null, branch_id: null, layer: 'source' }
const literal = '\uFEFF# 中😀\r\n<script>alert(1)</script> &#60;img&#62; https://evil.test\n'
function dossier() {
  const key = 'claim_' + 'd'.repeat(64), ref = 'ref_' + 'e'.repeat(64)
  const citation = { evidence_id: uuid, project_id: uuid, source_revision: uuid, source_name: '中😀', source_sha256: 'f'.repeat(64), source_byte_length: 100, source_codepoint_length: 10, source_recorded_at: '2026-10-04T00:00:00Z', start: 0, end: 2, offset_unit: 'unicode_codepoint', excerpt: '中😀', excerpt_sha256: 'a'.repeat(64), declared_page: 2 }
  return {
    schema_version: 1, mode: 'model_free_evidence_dossier', request: { schema_version: 1, title: '../unsafe 中😀', display_graph_ids: ['graph_1'], sections: [{ heading: 'Evidence', query: '中', top_k: 10 }], valid_at: null, recorded_before: null },
    sections: [{ ordinal: 1, heading: 'Evidence', source_claim_keys: [key], simulation_observation_keys: [], other_claim_keys: [] }],
    claims: [{ key, scope, provider_id: 'edge', kind: 'edge', claim_class: 'source', text: literal, predicate: 'p', source_node_id: 'a', target_node_id: 'b', episode_ids: [], evidence_ids: [uuid], reference_keys: [ref], created_at: null, valid_at: null, invalid_at: null, expired_at: null, claim_support_status: 'not_reviewed', reference_integrity: 'resolved' }],
    references: [{ key: ref, claim_key: key, scope, provider_id: 'edge', evidence_id: uuid, status: 'resolved', citation }],
    research_trace: [{ ordinal: 1, request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), query: '中', top_k: 10, display_graph_ids: ['graph_1'], valid_at: null, recorded_before: null, scopes: [{ display_graph_id: 'graph_1', scope, pages: 1, scanned: 1, eligible: 1, excluded: 0, unknown: 0, returned: 1, truncated: false }], passage_coverage: [], competing_claim_candidates: [], linked_citations: 1, resolved_citations: 1, unavailable_citations: 0, historical: false, historical_semantics: 'retained_edges_not_bitemporal_reconstruction', rank_basis: 'lexical_token_overlap' }],
    summary: { section_count: 1, query_count: 1, distinct_scoped_facts: 1, reference_links: 1, resolved_references: 1, unavailable_references: 0, query_reference_links: 1, query_resolved_references: 1, query_unavailable_references: 0, scanned_per_query_sum: 1, unknown_per_query_sum: 0, truncated_query_scopes: 0, passage_coverage: [], coverage_label: 'retrieved_passage_union_per_retained_revision' },
    input_sha256: 'a'.repeat(64), trace_sha256: 'b'.repeat(64), records_sha256: 'c'.repeat(64), model_generated: false, semantic_judge_used: false, claim_support_status: 'not_reviewed', consistency: 'individually_guarded_queries_not_atomic_snapshot', limitations: ['Lexical only; not an atomic snapshot'], markdown: literal
  }
}
test('literal UTF8 Markdown and complete detached JSON retain evidence and provenance with fixed names/MIME', () => {
  const result = dossier(), original = JSON.parse(JSON.stringify(result))
  const md = prepareDossierExport(result, 'markdown'), json = prepareDossierExport(result, 'json')
  assert.equal(md.ok, true); assert.equal(json.ok, true)
  assert.deepEqual(md.bytes, new TextEncoder().encode(literal))
  assert.equal(md.filename, 'nexaweave-evidence-dossier.md'); assert.equal(md.mime, 'text/markdown;charset=utf-8')
  assert.equal(json.filename, 'nexaweave-evidence-dossier.json'); assert.equal(json.mime, 'application/json;charset=utf-8')
  assert.deepEqual(JSON.parse(new TextDecoder().decode(json.bytes)), original)
  result.references[0].citation.excerpt = 'changed'
  assert.deepEqual(JSON.parse(new TextDecoder().decode(json.bytes)), original)
  assert.ok(new TextDecoder('utf-8', { ignoreBOM: true }).decode(md.bytes).includes('&#60;img&#62;'))
})
test('malformed modes, flags, counts, joins, scopes, Unicode and arbitrary formats fail closed', () => {
  const mutations = [r => { r.mode = 'research' }, r => { r.model_generated = true }, r => { r.summary.reference_links = 0 }, r => { r.references[0].claim_key = 'claim_' + 'f'.repeat(64) }, r => { r.references[0].scope = { ...scope, layer: 'simulation' } }, r => { r.markdown = '\ud800' }, r => { r.request.display_graph_ids = [] }, r => { r.secret = 'token' }]
  for (const mutate of mutations) { const r = dossier(); mutate(r); assert.deepEqual(prepareDossierExport(r, 'json'), { ok: false, code: 'invalid' }) }
  for (const format of ['html', '__proto__', 'https://evil.test']) assert.equal(prepareDossierExport(dossier(), format).ok, false)
  assert.equal(prepareDossierExport(null, 'markdown').ok, false)
})
test('final byte caps include multibyte UTF8 and JSON expansion; no clipping at or above the boundary', () => {
  const r = dossier(); r.markdown = 'x'.repeat(DOSSIER_EXPORT_LIMIT)
  const at = prepareDossierExport(r, 'markdown'); assert.equal(at.ok, true); assert.equal(at.bytes.length, DOSSIER_EXPORT_LIMIT)
  assert.deepEqual(prepareDossierExport(r, 'json'), { ok: false, code: 'tooLarge' })
  r.markdown = '😀'.repeat(DOSSIER_EXPORT_LIMIT / 4 + 1)
  assert.deepEqual(prepareDossierExport(r, 'markdown'), { ok: false, code: 'tooLarge' })
  r.markdown = '\n'.repeat(DOSSIER_EXPORT_LIMIT / 2)
  assert.deepEqual(prepareDossierExport(r, 'json'), { ok: false, code: 'tooLarge' })
})
