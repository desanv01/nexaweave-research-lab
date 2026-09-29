// Main-owned browser fixture: actual Vue component, synthetic data, no backend.
import { createApp, h } from 'vue'
import Step5Interaction from '../src/components/Step5Interaction.vue'
import service from '../src/api/index.js'
import i18n from '../src/i18n/index.js'

i18n.global.locale.value = 'en'
window.__fixtureErrors = []
window.addEventListener('error', (event) => window.__fixtureErrors.push(event.message))
window.addEventListener('unhandledrejection', (event) => window.__fixtureErrors.push(String(event.reason)))
const content = '## Synthetic evidence\n\n**Visible conclusion** — 研究 café.\n\n' +
  '3. Third item\n4. Fourth item\n   1. Nested item\n\n' +
  '```html\n<img src=x onerror="window.__xss=1">\n```\n\n' +
  '<svg onload="window.__xss=1">literal raw HTML</svg>\n\n' +
  '![inert remote image](https://remote.invalid/pixel)\n\n' +
  '[unsafe](javascript:alert(1)) [safe](https://example.com)'

service.defaults.adapter = async (config) => {
  let data
  if (config.url.endsWith('/agent-log')) {
    data = { logs: [
      { action: 'planning_complete', details: { outline: { title: 'Offline rendering QA', sections: [{ title: 'Synthetic evidence' }] } } },
      { action: 'section_complete', section_index: 1, details: { content } }
    ] }
  } else if (config.url === '/api/report/chat') {
    data = { response: content }
  } else if (config.url.includes('/profiles')) {
    data = { profiles: [] }
  } else if (config.url === '/api/report/fixture_report') {
    data = { report_id: 'fixture_report' }
  } else {
    throw new Error('Unexpected fixture request')
  }
  return { data: { success: true, data }, status: 200, statusText: 'OK', headers: {}, config }
}
createApp({ render: () => h(Step5Interaction, { reportId: 'fixture_report', simulationId: 'fixture_simulation' }) })
  .use(i18n).mount('#app')
