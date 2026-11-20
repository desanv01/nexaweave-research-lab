// The guarded driver owns the browser, authenticated exchanges and time budget.
const scopeKeys = ['schema_version', 'workspace_id', 'project_id', 'graph_id', 'run_id', 'branch_id', 'layer']
const timeFields = ['total_simulation_hours', 'minutes_per_round', 'agents_per_hour_min', 'agents_per_hour_max']
const periods = ['peak', 'off_peak', 'morning', 'work']
const platformFields = ['viral_threshold', 'recency_weight', 'popularity_weight', 'relevance_weight', 'echo_chamber_strength']
const platformNames = { twitter: 'Twitter', reddit: 'Reddit' }

export function browserMode(args) {
  if (args.length > 1 || (args.length && !['--twitter-only', '--reddit-only'].includes(args[0]))) {
    throw new Error('research_browser_mode_refused')
  }
  const singlePlatform = args.length ? args[0].slice(2, -5) : null
  const stages = ['connect', 'ontology-review', 'ontology-plan', 'ontology-execute', 'ontology-recover',
    'preparation-plan', 'preparation-start', 'preparation-ready', 'native-start', 'native-ready']
  if (!singlePlatform) stages.push('report-start', 'report-read', 'pdf-read-after-restart')
  stages.push('close')
  return { stages, singlePlatform }
}

export async function suggestPlatforms({ scope, options, singlePlatform, requireTrue, same }) {
  const platforms = singlePlatform ? [singlePlatform] : ['twitter', 'reddit']
  requireTrue(same(options.platforms, platforms))
  const twitter = scope.getByLabel('Twitter', { exact: true })
  const reddit = scope.getByLabel('Reddit', { exact: true })
  const startOnTwitter = singlePlatform !== 'twitter'
  await twitter.setChecked(startOnTwitter)
  await reddit.setChecked(!startOnTwitter)
  requireTrue(await twitter.isChecked() === startOnTwitter && await reddit.isChecked() === !startOnTwitter)
  await scope.locator('#preparation-requirement').fill(options.simulation_requirement)
  await scope.locator('.suggest').click()
  requireTrue(await twitter.isChecked() === platforms.includes('twitter') &&
    await reddit.isChecked() === platforms.includes('reddit'))
  requireTrue(await scope.locator('.plan').count() === 0)
}

export async function inspectReadyConfiguration({
  page, preparation, route, url, graph, project, singlePlatform, exchange, enabled, requireTrue, keys, same, sha
}) {
  const panel = page.locator('.preparation-workbench .prepared-configuration')
  const button = panel.locator('button.inspect-configuration')
  const result = panel.locator('.prepared-configuration-result')
  let requests = 0
  const observe = request => {
    if (request.url() === url && request.method() === 'POST') requests++
  }
  page.on('request', observe)
  try {
    await enabled(button)
    requireTrue(requests === 0 && await result.count() === 0)
    const observed = await exchange(page, route, 'POST', () => button.click(), 263168)
    const request = {
      schema_version: 1, operation_id: preparation.operation_id, plan_sha256: preparation.plan_sha256,
      source_revision: preparation.source.source_revision, artifact_sha256: preparation.receipt.artifact_sha256
    }
    const value = observed.value
    keys(observed.request, Object.keys(request))
    keys(value, [...Object.keys(request), 'display_graph_id', 'scope', 'project_revision', 'goal',
      'time_activity', 'hot_topics', 'hot_topics_omitted', 'platform_controls', 'effective_controls'])
    keys(value.scope, scopeKeys)
    requireTrue(Object.keys(request).every(key => observed.request[key] === request[key] && value[key] === request[key]))
    requireTrue(request.source_revision === preparation.sources[0].source_revision &&
      value.display_graph_id === graph && value.display_graph_id === preparation.display_graph_id &&
      value.scope.project_id === project && scopeKeys.every(key => value.scope[key] === preparation.scope[key]) &&
      value.project_revision === preparation.project_revision && value.goal === preparation.options.simulation_requirement)

    const effective = value.effective_controls
    keys(effective, ['platforms', 'seed', 'max_rounds'])
    const platforms = singlePlatform ? [singlePlatform] : ['twitter', 'reddit']
    requireTrue(same(effective.platforms, platforms) && same(effective.platforms, preparation.options.platforms) &&
      effective.seed === preparation.options.seed && effective.max_rounds === 1 && effective.max_rounds === preparation.options.max_rounds)

    const activity = value.time_activity
    const controls = value.platform_controls
    const omitted = value.hot_topics_omitted
    const unavailable = 'No saved proposal is available.'
    const notes = []
    if (activity === null) notes.push(unavailable)
    if (omitted === null) notes.push(unavailable)
    else if (omitted > 0) notes.push(`Additional topics omitted (${omitted}).`)
    else if (!value.hot_topics.length) notes.push('None')
    if (controls === null) notes.push(unavailable)
    const expected = {
      goal: value.goal,
      effective: [platforms.map(platform => platformNames[platform]).join(', '), String(effective.seed), String(effective.max_rounds)],
      time: activity === null ? [] : [
        ...timeFields.map(key => String(activity[key])),
        ...periods.flatMap(period => [
          activity[period + '_hours'].join(', ') || 'None', String(activity[period + '_activity_multiplier'])
        ])
      ],
      topics: value.hot_topics,
      platforms: (controls ?? []).map(control => ({
        platform: control.platform, values: platformFields.map(key => String(control[key]))
      })),
      notes
    }
    await panel.locator('.feedback').filter({ hasText: /^Saved configuration loaded\.$/ }).waitFor({ state: 'visible', timeout: 15000 })
    await result.waitFor({ state: 'visible', timeout: 15000 })
    // Return a digest instead of moving generated text over the browser wire.
    const rendered = await result.evaluate(async node => {
      const text = selector => node.querySelector(selector)?.textContent ?? null
      const list = (root, selector) => [...root.querySelectorAll(selector)].map(item => item.textContent)
      const snapshot = {
        goal: text('.configuration-goal'),
        effective: list(node, '.effective-controls dd'),
        time: list(node, '.proposed-time-activity dd'),
        topics: list(node, '.proposed-hot-topics > li'),
        platforms: [...node.querySelectorAll('.proposed-platform-controls')].map(item => ({
          platform: item.dataset.platform, values: list(item, 'dd')
        })),
        notes: list(node, ':scope > p')
      }
      const bytes = new TextEncoder().encode(JSON.stringify(snapshot))
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
      return Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('')
    })
    requireTrue(requests === 1 && rendered === sha(JSON.stringify(expected)))
  } finally {
    page.off('request', observe)
  }
}
