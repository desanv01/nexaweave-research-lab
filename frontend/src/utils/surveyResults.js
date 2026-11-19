import { MAX_MARKDOWN_CHARS } from './safeMarkdown.js'

export const MAX_SURVEY_EXPORT_BYTES = 1024 * 1024

const surveyPlatform = value => value === 'twitter' || value === 'reddit' ? value : null
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const text = value => typeof value === 'string' && value.trim() ? value : null
const actorId = value => {
  if (typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value)) value = Number(value)
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}

export function availableSurveyPlatforms(response, simulationId) {
  const state = response?.data
  if (response?.success !== true || !record(state) || state.simulation_id !== simulationId ||
      state.env_alive !== true || typeof state.twitter_available !== 'boolean' ||
      typeof state.reddit_available !== 'boolean') return []

  const platforms = []
  if (state.twitter_available) platforms.push('twitter')
  if (state.reddit_available) platforms.push('reddit')
  return platforms
}

export function captureSurvey(simulationId, question, selection, profiles) {
  const actors = Array.from(selection, id => {
    const profile = profiles[id]
    return {
      agent_id: id,
      agent_name: text(profile?.username),
      profession: text(profile?.profession)
    }
  })
  return { simulation_id: simulationId, question, actors }
}

export function surveyResultSnapshot(survey, data, availablePlatforms) {
  const result = data?.result ?? data
  const entries = result?.results ?? result
  const onlyPlatform = availablePlatforms.length === 1 ? availablePlatforms[0] : null
  const legacyPlatform = surveyPlatform(result?.platform) ?? surveyPlatform(data?.platform) ?? onlyPlatform
  const selected = new Set(survey.actors.map(actor => actor.agent_id))
  const expectedPlatforms = new Set(availablePlatforms)
  const byActor = new Map()

  const addReply = (id, row, keyedPlatform = null) => {
    if (!selected.has(id) || !record(row)) return
    if (Object.hasOwn(row, 'agent_id') && actorId(row.agent_id) !== id) return
    const explicitPlatform = surveyPlatform(row.platform)
    if (row.platform != null && (!explicitPlatform || (keyedPlatform && keyedPlatform !== explicitPlatform))) return
    if (keyedPlatform) expectedPlatforms.add(keyedPlatform)
    const platform = keyedPlatform ?? explicitPlatform ?? legacyPlatform
    const candidate = text(row.response) ?? text(row.answer)
    const answer = !row.error && candidate?.length <= MAX_MARKDOWN_CHARS ? candidate : null
    const rows = byActor.get(id) ?? new Map()
    // An explicit platform key takes precedence over a legacy row in the same slot.
    if (!rows.get(platform)?.answer || (keyedPlatform && answer)) {
      const timestamp = Number.isFinite(row.timestamp) ? row.timestamp : text(row.timestamp)
      rows.set(platform, { answer, timestamp })
    }
    byActor.set(id, rows)
  }

  if (Array.isArray(entries)) {
    for (const row of entries) {
      if (record(row) && Object.hasOwn(row, 'agent_id')) addReply(actorId(row.agent_id), row)
    }
  } else if (record(entries)) {
    for (const [key, row] of Object.entries(entries)) {
      const match = /^(?:(twitter|reddit)_)?(0|[1-9]\d*)$/.exec(key)
      if (!match) continue
      addReply(actorId(match[2]), row, surveyPlatform(match[1]))
    }
  }

  const replies = survey.actors.flatMap(actor => {
    const answers = byActor.get(actor.agent_id)
    const platforms = new Set([...expectedPlatforms, ...(answers?.keys() ?? [])])
    if (!platforms.size) platforms.add(legacyPlatform)
    return [...platforms].map(platform => {
      const answer = answers?.get(platform)?.answer ?? null
      return {
        ...actor,
        platform,
        timestamp: answers?.get(platform)?.timestamp ?? null,
        status: answer === null ? 'missing' : 'received',
        answer
      }
    })
  })

  return { schema_version: 1, simulation_id: survey.simulation_id, question: survey.question, replies }
}

export function serializeSurveyResults(snapshot) {
  if (snapshot.replies.length > MAX_SURVEY_EXPORT_BYTES / 100) return null
  let characters = snapshot.simulation_id.length + snapshot.question.length
  for (const row of snapshot.replies) {
    characters += (row.agent_name?.length ?? 0) + (row.profession?.length ?? 0) +
      (row.timestamp?.length ?? 0) + (row.answer?.length ?? 0)
    if (characters > MAX_SURVEY_EXPORT_BYTES) return null
  }
  const bytes = new TextEncoder().encode(JSON.stringify(snapshot))
  return bytes.length <= MAX_SURVEY_EXPORT_BYTES ? bytes : null
}

export function downloadSurveyResults(bytes) {
  const anchor = document.createElement('a')
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/json;charset=utf-8' }))
  anchor.href = url
  anchor.download = 'nexaweave-survey-results.json'
  try {
    document.body.append(anchor)
    anchor.click()
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  } finally {
    anchor.remove()
  }
  let active = true
  const release = () => {
    if (!active) return
    active = false
    clearTimeout(timer)
    URL.revokeObjectURL(url)
  }
  const timer = setTimeout(release, 1000)
  return release
}
