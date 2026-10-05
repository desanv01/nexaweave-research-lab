// Population DTO admission and native producer mappings. No transport or secrets.
const invalid = (code = 'invalid_reply') => { const error = new Error(code); error.code = code; throw error }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype
const fields = (value, keys) => { if (!object(value) || Object.keys(value).sort().join('|') !== keys.split(' ').sort().join('|')) invalid() }
const uuid = value => { if (typeof value !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)) invalid() }
const text = (value, maximum = 32768) => { if (typeof value !== 'string' || [...value].length > maximum || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) invalid() }
const integer = (value, maximum = Number.MAX_SAFE_INTEGER, minimum = 0) => { if (!Number.isSafeInteger(value) || value < minimum || value > maximum) invalid() }
const list = (value, maximum, validate) => { if (!Array.isArray(value) || value.length > maximum) invalid(); for (let index = 0; index < value.length; index++) validate(value[index], index) }
const ids = value => list(value, 10000, uuid)
const nullable = (value, validate) => { if (value !== null) validate(value) }
const synthetic = ['user_name', 'bio', 'persona', 'age', 'gender', 'mbti', 'country', 'profession', 'interested_topics', 'karma', 'friend_count', 'follower_count', 'statuses_count']
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
function date(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000') || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value) invalid()
}
function jsonValue(value, depth = 0) {
  if (depth > 32) invalid()
  if (value === null || typeof value === 'boolean') return
  if (typeof value === 'string') { text(value, 2097152); return }
  if (typeof value === 'number') { if (!Number.isFinite(value)) invalid(); return }
  if (Array.isArray(value)) { list(value, 10000, item => jsonValue(item, depth + 1)); return }
  if (!object(value) || Object.keys(value).length > 10000) invalid()
  for (const [key, item] of Object.entries(value)) { text(key, 32768); jsonValue(item, depth + 1) }
}
export function populationOptions(value = {}, platform) {
  try {
    if (!object(value) || Object.keys(value).some(key => !['types', 'max_agents', 'seed'].includes(key))) invalid()
    const options = { max_agents: value.max_agents === undefined ? 10 : value.max_agents, seed: value.seed === undefined ? 0 : value.seed }
    integer(options.max_agents, 100, 1); integer(options.seed, 4294967295)
    if (value.types !== undefined) {
      list(value.types, 50, label => { if (typeof label !== 'string' || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(label) || ['Entity', 'Node'].includes(label)) invalid() })
      if (new Set(value.types).size !== value.types.length) invalid()
      if (value.types.length) options.types = [...value.types]
    }
    if (platform !== undefined) { if (!['twitter', 'reddit'].includes(platform)) invalid(); options.platform = platform }
    if (new TextEncoder().encode(JSON.stringify(options)).length > 16384) invalid()
    return options
  } catch { invalid('invalid_request') }
}
export function populationTypeLabels(labels) {
  if (!Array.isArray(labels)) return []
  return [...new Set(labels.filter(label => typeof label === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(label) && !['Entity', 'Node'].includes(label)))].sort().slice(0, 50)
}
export function validatePopulationPreview(value, graph, submitted = {}) {
  const options = populationOptions(submitted)
  fields(value, 'graph_id generator enrichment llm_used simulation_executed snapshot_consistent profile_date eligible_count selected_count synthetic_fields profiles grounding')
  if (typeof graph !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(graph)) invalid()
  if (value.graph_id !== graph || value.generator !== 'inherited_rule_based_v1' || value.enrichment !== 'none' || value.llm_used !== false || value.simulation_executed !== false || value.snapshot_consistent !== false || !same(value.synthetic_fields, synthetic)) invalid()
  date(value.profile_date); integer(value.eligible_count, 10000, 1); integer(value.selected_count, options.max_agents, 1)
  if (value.selected_count !== Math.min(value.eligible_count, options.max_agents)) invalid()
  list(value.profiles, 100, (profile, index) => {
    fields(profile, 'user_id user_name name bio persona karma friend_count follower_count statuses_count age gender mbti country profession interested_topics source_entity_uuid source_entity_type created_at')
    if (profile.user_id !== index || profile.created_at !== value.profile_date) invalid()
    for (const key of ['user_name', 'name', 'bio', 'persona']) text(profile[key], 2097152)
    if (!profile.user_name || !profile.bio || !profile.persona) invalid()
    for (const key of ['karma', 'friend_count', 'follower_count', 'statuses_count']) integer(profile[key])
    nullable(profile.age, age => integer(age)); for (const key of ['gender', 'mbti', 'country', 'profession']) nullable(profile[key], item => text(item, 2097152))
    list(profile.interested_topics, 10000, item => text(item, 2097152)); uuid(profile.source_entity_uuid); text(profile.source_entity_type, 128)
    if (['Entity', 'Node', ''].includes(profile.source_entity_type)) invalid()
  })
  const profiles = value.profiles
  if (profiles.length !== value.selected_count || new Set(profiles.map(p => p.user_name)).size !== profiles.length || new Set(profiles.map(p => p.source_entity_uuid)).size !== profiles.length || !object(value.grounding) || Object.keys(value.grounding).length !== profiles.length) invalid()
  profiles.forEach((profile, index) => {
    if (index && profiles[index - 1].source_entity_uuid >= profile.source_entity_uuid) invalid()
    const ground = value.grounding[profile.source_entity_uuid]
    fields(ground, 'source_entity_uuid labels summary attributes episode_ids evidence_ids facts')
    if (ground.source_entity_uuid !== profile.source_entity_uuid) invalid()
    list(ground.labels, 64, label => text(label, 128)); text(ground.summary); if (!object(ground.attributes)) invalid(); jsonValue(ground.attributes)
    if (new Set(ground.labels).size !== ground.labels.length || ground.labels.some(label => !label || /\p{Cc}/u.test(label))) invalid()
    const custom = ground.labels.filter(label => !['Entity', 'Node'].includes(label))
    if (custom[0] !== profile.source_entity_type || (options.types && !custom.some(label => options.types.includes(label)))) invalid()
    ids(ground.episode_ids); ids(ground.evidence_ids)
    list(ground.facts, 10000, fact => {
      fields(fact, 'edge_uuid direction edge_name source_node_uuid target_node_uuid fact episode_ids evidence_ids')
      for (const key of ['edge_uuid', 'source_node_uuid', 'target_node_uuid']) uuid(fact[key])
      text(fact.edge_name, 2097152); text(fact.fact, 2097152); ids(fact.episode_ids); ids(fact.evidence_ids)
      if (fact.direction === 'outgoing') { if (fact.source_node_uuid !== profile.source_entity_uuid) invalid() }
      else if (fact.direction === 'incoming') { if (fact.target_node_uuid !== profile.source_entity_uuid || fact.source_node_uuid === profile.source_entity_uuid) invalid() }
      else invalid()
    })
    if (new Set(ground.facts.map(f => f.edge_uuid)).size !== ground.facts.length || ground.facts.some((fact, i) => i && ground.facts[i - 1].edge_uuid > fact.edge_uuid)) invalid()
  })
  // The same incident edge must agree across selected endpoints.
  const edges = new Map()
  Object.values(value.grounding).forEach(g => g.facts.forEach(f => {
    const record = { ...f }; delete record.direction
    if (edges.has(f.edge_uuid) && !same(edges.get(f.edge_uuid), record)) invalid()
    edges.set(f.edge_uuid, record)
    const other = f.direction === 'outgoing' ? f.target_node_uuid : f.source_node_uuid
    if (other !== g.source_entity_uuid && value.grounding[other] && !value.grounding[other].facts.some(item => item.edge_uuid === f.edge_uuid && item.direction !== f.direction)) invalid()
  }))
  if (new TextEncoder().encode(JSON.stringify(value)).length > 2097152) invalid()
  return JSON.parse(JSON.stringify(value))
}
export function redditProfile(profile) {
  const result = { user_id: profile.user_id, username: profile.user_name, name: profile.name, bio: profile.bio, persona: profile.persona, karma: profile.karma, created_at: profile.created_at }
  for (const key of ['age', 'gender', 'mbti', 'country', 'profession']) if (profile[key]) result[key] = profile[key]
  if (profile.interested_topics.length) result.interested_topics = [...profile.interested_topics]
  return result
}
export function twitterProfile(profile, index) {
  const normalize = value => value.replace(/\n/g, ' ').replace(/\r/g, ' ')
  const character = profile.persona && profile.persona !== profile.bio ? `${profile.bio} ${profile.persona}` : profile.bio
  return [String(index), profile.name, profile.user_name, normalize(character), normalize(profile.bio)]
}
export function parsePopulationCsv(raw) {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > 2097152) invalid()
  const rows = []; let row = [], cell = '', quoted = false, closed = false, atStart = true
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i]
    if (quoted) { if (c === '"') { if (raw[i + 1] === '"') { cell += '"'; i++ } else { quoted = false; closed = true } } else cell += c; continue }
    if (c === '"') { if (!atStart || closed) invalid(); quoted = true; atStart = false; continue }
    if (c === ',' || c === '\r' || c === '\n') {
      row.push(cell); cell = ''; closed = false; atStart = true
      if (row.length > 5) invalid()
      if (c !== ',') { if (c === '\r' && raw[i + 1] === '\n') i++; rows.push(row); row = []; if (rows.length > 101) invalid() }
    } else { if (closed) invalid(); cell += c; atStart = false }
  }
  if (quoted) invalid()
  if (cell || row.length || closed || !atStart) { row.push(cell); rows.push(row) }
  return rows
}
export function validatePopulationExport(raw, platform, preview) {
  if (typeof raw !== 'string' || new TextEncoder().encode(raw).length > 2097152) invalid()
  if (!preview || !Array.isArray(preview.profiles) || !['twitter', 'reddit'].includes(platform)) invalid()
  const expected = platform === 'twitter' ? [['user_id', 'name', 'username', 'user_char', 'description'], ...preview.profiles.map(twitterProfile)] : preview.profiles.map(redditProfile)
  let actual
  if (platform === 'twitter') actual = parsePopulationCsv(raw)
  else {
    // Reject duplicate keys, including escaped spellings, without accepting a
    // different JSON object than the browser parser would display.
    const stack = []
    for (let i = 0; i < raw.length; i++) {
      const c = raw[i]
      if (c === '"') {
        const start = i; for (i++; i < raw.length; i++) { if (raw[i] === '\\') i++; else if (raw[i] === '"') break }
        const top = stack.at(-1); if (top?.object && top.key) { const key = JSON.parse(raw.slice(start, i + 1)); if (top.keys.has(key)) invalid(); top.keys.add(key); top.key = false }
      } else if (c === '{' || c === '[') { stack.push({ object: c === '{', key: c === '{', keys: new Set() }); if (stack.length > 32) invalid() }
      else if (c === '}' || c === ']') stack.pop()
      else if (c === ',' && stack.at(-1)?.object) stack.at(-1).key = true
    }
    actual = JSON.parse(raw)
    if (!Array.isArray(actual) || actual.length !== expected.length) invalid()
    actual.forEach((item, index) => { fields(item, Object.keys(expected[index]).join(' ')); for (const key of Object.keys(item)) if (!same(item[key], expected[index][key])) invalid() })
    return actual.length
  }
  if (!same(actual, expected)) invalid()
  return preview.profiles.length
}
