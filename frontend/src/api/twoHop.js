const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const encoder = new TextEncoder()
const requestKeys = ['schema_version', 'operation', 'display_graph_id', 'source_node_id', 'target_node_id']
const unavailable = ['no_path_in_scan', 'selected_path_evidence_unavailable']

export function twoHopPayload(value, graph, fail) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== requestKeys.length || !requestKeys.every(key => Object.hasOwn(value, key))
    || value.schema_version !== 2 || value.operation !== 'two_hop' || value.display_graph_id !== graph
    || ![value.source_node_id, value.target_node_id].every(id => typeof id === 'string' && uuid.test(id))
    || value.source_node_id === value.target_node_id) fail('invalid_request')

  const raw = JSON.stringify(Object.fromEntries(requestKeys.map(key => [key, value[key]])))
  if (encoder.encode(raw).length > 16384) fail('invalid_request')
  return raw
}

export async function validateTwoHopResult(value, request, checks) {
  const { shape, coverage, passage, citation, text, awareTimestamp, fail } = checks
  shape(value, 'schema_version operation source_node_id target_node_id path path_status scope_coverage passage_coverage historical historical_semantics rank_basis')
  const { path, path_status: status, scope_coverage: scan, passage_coverage: passages } = value
  coverage(scan)
  if (value.schema_version !== 2 || value.operation !== 'two_hop'
    || value.source_node_id !== request.source_node_id || value.target_node_id !== request.target_node_id
    || scan.display_graph_id !== request.display_graph_id || value.historical !== false
    || value.historical_semantics !== 'retained_edges_not_bitemporal_reconstruction'
    || value.rank_basis !== 'directed_path_edge_id_order' || !Array.isArray(passages) || passages.length > 200) fail()

  if (unavailable.includes(status)) {
    if (path !== null || scan.returned !== 0 || passages.length !== 0) fail()
    return value
  }
  if (status !== 'found' || scan.returned !== 2) fail()
  shape(path, 'intermediate_node_id hops')
  const middle = path.intermediate_node_id
  text(middle, 32768, 1)
  if (middle === request.source_node_id || middle === request.target_node_id
    || !Array.isArray(path.hops) || path.hops.length !== 2) fail()

  const nodes = [request.source_node_id, middle, request.target_node_id]
  const citations = path.hops.flatMap((edge, index) => {
    shape(edge, 'provider_id name fact source_node_id target_node_id episode_ids evidence_ids citations valid_at invalid_at expired_at created_at')
    text(edge.provider_id, 32768, 1)
    if (edge.name !== null) text(edge.name, 256)
    text(edge.fact, 32768, 1)
    if (edge.source_node_id !== nodes[index] || edge.target_node_id !== nodes[index + 1]
      || !Array.isArray(edge.episode_ids) || edge.episode_ids.length < 1 || edge.episode_ids.length > 100
      || !Array.isArray(edge.evidence_ids) || edge.evidence_ids.length < 1 || edge.evidence_ids.length > 100
      || !Array.isArray(edge.citations) || edge.citations.length !== edge.evidence_ids.length) fail()
    edge.episode_ids.forEach(id => text(id))
    for (const key of ['valid_at', 'invalid_at', 'expired_at', 'created_at']) {
      if (edge[key] !== null) {
        try { awareTimestamp(edge[key]) } catch { fail() }
      }
    }
    edge.citations.forEach((item, index) => {
      try { citation(item) } catch { fail() }
      if (item.evidence_id !== edge.evidence_ids[index]) fail()
    })
    return edge.citations
  })
  if (path.hops[0].provider_id === path.hops[1].provider_id) fail()
  passages.forEach(passage)

  try {
    const subtle = globalThis.crypto.subtle
    for (const item of citations) {
      const digest = new Uint8Array(await subtle.digest('SHA-256', encoder.encode(item.excerpt)))
      const hex = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('')
      if (hex !== item.excerpt_sha256) fail()
    }
  } catch {
    fail()
  }
  return value
}
