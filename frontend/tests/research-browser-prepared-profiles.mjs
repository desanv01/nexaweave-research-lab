// The guarded driver owns the browser, authenticated exchanges and time budget.
export async function inspectPreparedProfiles({ page, preparation, route, url, exchange, enabled, requireTrue, same, sha }) {
  requireTrue(same(preparation.options.platforms, ['twitter', 'reddit']))
  const person = preparation.actors.find(actor => actor.labels.includes('Person'))
  const organization = preparation.actors.find(actor => actor.labels.includes('Organization') && actor !== person)
  requireTrue(person && organization && person.source_entity_uuid !== organization.source_entity_uuid)

  const panel = page.locator('.preparation-workbench .prepared-profiles')
  const picker = panel.locator('#prepared-profile-actor'), button = panel.locator('.inspect-profile')
  const result = panel.locator('.prepared-profile-result')
  await enabled(picker)
  requireTrue(await picker.inputValue() === '' && await button.isDisabled() && await result.count() === 0)
  let requests = 0
  const observe = request => {
    if (request.url() === url && request.method() === 'POST') requests++
  }
  page.on('request', observe)
  try {
    for (const actor of [person, organization]) {
      const prior = requests
      await picker.selectOption(actor.source_entity_uuid)
      await result.waitFor({ state: 'hidden', timeout: 15000 })
      await enabled(button)
      requireTrue(requests === prior)
      const observed = await exchange(page, route, 'POST', () => button.press('Enter'), 263168)
      const request = { schema_version: 1, operation_id: preparation.operation_id,
        plan_sha256: preparation.plan_sha256, source_entity_uuid: actor.source_entity_uuid }
      const value = observed.value, support = value.frozen_support
      const ordinal = preparation.actors.indexOf(actor)
      requireTrue(requests === prior + 1 && same(observed.request, request))
      requireTrue(Object.keys(request).every(key => value[key] === request[key]) &&
        value.display_graph_id === preparation.display_graph_id && same(value.scope, preparation.scope) &&
        value.project_revision === preparation.project_revision &&
        value.artifact_sha256 === preparation.receipt.artifact_sha256 &&
        same(value.actor, { ordinal, name: actor.name, labels: actor.labels }))
      requireTrue(support.source_entity_uuid === actor.source_entity_uuid &&
        (support.evidence_ids.length > 0 || support.facts.some(fact => fact.evidence_ids.length > 0)))
      requireTrue(same(value.generated_profiles.map(profile => profile.platform), ['twitter', 'reddit']))
      requireTrue(value.generated_profiles.every(p => [p.handle, p.description, p.persona].every(text => text.trim())))

      const expected = {
        name: actor.name, labels: actor.labels.join(', '), uuid: actor.source_entity_uuid,
        profiles: value.generated_profiles.map(p => ({
          platform: p.platform, handle: p.handle, description: p.description, persona: p.persona
        })),
        summary: support.summary || null, evidence: support.evidence_ids,
        facts: support.facts.map(fact => ({ text: fact.fact, evidence: fact.evidence_ids }))
      }
      await result.waitFor({ state: 'visible', timeout: 15000 })
      // Hash rendered fields in Chromium so no profile text crosses the command wire.
      const rendered = await result.evaluate(async node => {
        const text = (root, selector) => root.querySelector(selector)?.textContent ?? null
        const list = (root, selector) => [...root.querySelectorAll(selector)].map(item => item.textContent)
        const snapshot = {
          name: text(node, '.profile-name'), labels: text(node, '.profile-actor-labels'),
          uuid: text(node, '.profile-actor-uuid'),
          profiles: [...node.querySelectorAll('.saved-platform-profile')].map(item => ({
            platform: item.dataset.platform, handle: text(item, '.profile-handle'),
            description: text(item, '.profile-description'), persona: text(item, '.profile-persona')
          })),
          summary: text(node, '.profile-summary'), evidence: list(node, '.profile-evidence > li'),
          facts: [...node.querySelectorAll('.profile-fact')].map(item => ({
            text: text(item, 'p'), evidence: list(item, 'ul > li')
          }))
        }
        const bytes = new TextEncoder().encode(JSON.stringify(snapshot))
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
        return {
          sha256: Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join(''),
          literal: node.querySelector('script, iframe, img, a') === null
        }
      })
      requireTrue(rendered.literal && rendered.sha256 === sha(JSON.stringify(expected)))
    }
    requireTrue(requests === 2)
  } finally {
    page.off('request', observe)
  }
}
