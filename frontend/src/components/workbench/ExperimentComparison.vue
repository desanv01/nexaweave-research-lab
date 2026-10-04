<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { experimentSelection, validateExperimentCatalog, validateExperimentComparison } from '../../api/experimentComparison.js'
import { copyFor } from '../../i18n/workbench.js'
const props = defineProps({ methods: { type: Object, required: true }, connected: Boolean, busy: Boolean, resetVersion: Number, locale: { type: String, default: 'en' } })
const copy = computed(() => copyFor(props.locale).experiments)
const catalog = ref(null), selected = ref([]), title = ref(''), result = ref(null), pending = ref(false), message = ref(''), error = ref('')
const feedback = ref(null), resultsPanel = ref(null)
let generation = 0
const disabled = computed(() => !props.connected || props.busy || pending.value)
const bytes = computed(() => new TextEncoder().encode(title.value).length)
const titleInvalid = computed(() => title.value !== '' && (bytes.value > 160 || !title.value.trim() || /[\u0000-\u001f\u007f]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(title.value)))
const ordered = computed(() => catalog.value?.members.filter(m => selected.value.includes(m.member_id)).map(m => m.member_id) || [])
const valid = computed(() => { try { experimentSelection({ version: 1, title: title.value, member_ids: ordered.value }, catalog.value); return true } catch { return false } })
const statusText = computed(() => pending.value ? copy.value.working : error.value ? copy.value[error.value] || copy.value.failed : copy.value[message.value] || copy.value.idle)
const metricText = value => value === null ? copy.value.unavailable : String(value)
const stateText = state => copy.value.states[state] || copy.value.unavailable
function clear() { generation++; catalog.value = null; selected.value = []; title.value = ''; result.value = null; pending.value = false; message.value = ''; error.value = '' }
watch(() => [props.connected, props.resetVersion], clear, { flush: 'sync' })
watch([title, selected], () => { generation++; result.value = null; error.value = ''; message.value = catalog.value ? 'loaded' : ''; pending.value = false }, { deep: true, flush: 'sync' })
onBeforeUnmount(clear)
function errorKey(code) {
  if (['invalid_request', 'limit_exceeded'].includes(code)) return 'invalid'
  if (['invalid_reply', 'result_too_large'].includes(code)) return 'invalidReply'
  if (['unauthorized', 'origin_denied'].includes(code)) return 'denied'
  if (code === 'cancelled') return 'cancelled'
  if (code === 'experiment_unavailable') return 'unavailableHost'
  return 'failed'
}
async function load() {
  if (disabled.value) return
  // Clear the old window before refresh; no stale result or selection survives.
  clear()
  const epoch = ++generation
  pending.value = true
  try {
    const admitted = validateExperimentCatalog(await props.methods.catalog())
    if (epoch !== generation || !props.connected) return
    catalog.value = JSON.parse(JSON.stringify(admitted)); message.value = 'loaded'
  } catch (e) { if (epoch === generation && props.connected) error.value = errorKey(e.code) }
  finally { if (epoch === generation) pending.value = false }
}
async function compare() {
  if (disabled.value || !catalog.value) return
  result.value = null; message.value = ''; error.value = ''
  let snapshot
  try { snapshot = experimentSelection({ version: 1, title: title.value, member_ids: ordered.value }, catalog.value) }
  catch { error.value = 'invalid'; await nextTick(); feedback.value?.focus(); return }
  const epoch = ++generation
  pending.value = true
  try {
    const admitted = validateExperimentComparison(await props.methods.compare(snapshot.payload, snapshot.catalog), snapshot.catalog, snapshot.payload)
    if (epoch !== generation || !props.connected) return
    result.value = JSON.parse(JSON.stringify(admitted)); message.value = 'completed'
    await nextTick(); if (epoch === generation) resultsPanel.value?.focus()
  } catch (e) {
    if (epoch === generation && props.connected) { error.value = errorKey(e.code); await nextTick(); feedback.value?.focus() }
  } finally { if (epoch === generation) pending.value = false }
}
</script>
<template>
  <section class="experiment-comparison" aria-labelledby="experiment-title">
    <div class="section-heading"><div><h2 id="experiment-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p></div><button type="button" class="catalog-load" :disabled="disabled" @click="load">{{ copy.load }}</button></div>
    <p class="help">{{ copy.authority }}</p>
    <p ref="feedback" class="feedback" tabindex="-1" role="status" aria-live="polite" aria-atomic="true">{{ statusText }}</p>
    <template v-if="catalog">
      <dl class="catalog-identity"><div><dt>{{ copy.project }}</dt><dd class="mono">{{ catalog.project_id }}</dd></div><div><dt>{{ copy.revision }}</dt><dd>{{ catalog.project_revision }}</dd></div></dl>
      <details class="catalog-provenance"><summary>{{ copy.cohort }}</summary><dl><dt>{{ copy.cohortDigest }}</dt><dd class="mono">{{ catalog.cohort_manifest_digest }}</dd><dt>{{ copy.publicDigest }}</dt><dd class="mono">{{ catalog.public_projection_digest }}</dd></dl><p>{{ copy.fingerprints }}</p></details>
      <form @submit.prevent="compare">
        <fieldset :disabled="disabled"><legend>{{ copy.selection }}</legend>
          <p id="experiment-selection-help" class="help">{{ copy.selectionHint }} · {{ ordered.length }} / 16</p>
          <div class="member-grid">
            <article v-for="(member, index) in catalog.members" :key="member.member_id" class="catalog-member">
              <label class="member-choice" :for="`experiment-member-${index}`"><input :id="`experiment-member-${index}`" v-model="selected" type="checkbox" :value="member.member_id" aria-describedby="experiment-selection-help"><span>{{ member.member_label }}</span></label>
              <p>{{ copy.case }}: {{ member.case_label }}</p><p>{{ copy.state }}: {{ stateText(member.state) }}</p><p>{{ copy.cancelIntent }}: {{ member.cancel_requested ? copy.yes : copy.no }}</p><p>{{ copy.seed }}: <span class="mono seed">{{ member.seed }}</span></p>
              <details><summary>{{ copy.memberIdentity }}</summary><dl><dt>{{ copy.memberId }}</dt><dd class="mono">{{ member.member_id }}</dd><dt>{{ copy.run }}</dt><dd class="mono">{{ member.run_id }}</dd><dt>{{ copy.rounds }}</dt><dd>{{ member.max_rounds }}</dd><dt>{{ copy.platforms }}</dt><dd>{{ member.platforms.join(', ') }}</dd></dl></details>
            </article>
          </div>
          <label class="title-field" for="experiment-comparison-title">{{ copy.comparisonTitle }}<input id="experiment-comparison-title" v-model="title" maxlength="160" required autocomplete="off" aria-describedby="experiment-title-help" :aria-invalid="titleInvalid || error === 'invalid'"></label>
          <p id="experiment-title-help" class="help">{{ copy.titleHint }} · {{ bytes }} / 160 {{ copy.bytes }}</p>
          <p v-if="titleInvalid || error === 'invalid'" class="field-error">{{ copy.invalid }}</p>
          <button class="primary compare-submit" type="submit" :disabled="!valid">{{ copy.compare }}</button>
        </fieldset>
      </form>
    </template>
    <section v-if="result" ref="resultsPanel" tabindex="-1" class="comparison-result" aria-labelledby="comparison-result-title">
      <h3 id="comparison-result-title">{{ result.title }}</h3><p>{{ copy.observed }}</p>
      <dl class="accounting"><div v-for="(count, state) in result.accounting" :key="state"><dt>{{ stateText(state) }}</dt><dd>{{ count }}</dd></div></dl>
      <p>{{ copy.cancelIntent }}: {{ result.cancellation_intent.cancel_requested_count }} · {{ copy.cancelOverlap }}</p><p>{{ copy.declaredSeeds }}: {{ result.distinct_declared_seed_count }} · {{ copy.successfulSeeds }}: {{ result.distinct_successful_seed_count }}</p>
      <p class="limitations">{{ copy.limitations }}</p><p>{{ copy.spend }}: {{ copy.unknown }}</p>
      <div class="member-grid">
        <article v-for="member in result.members" :key="member.member_id" class="result-member">
          <h4>{{ member.member_label }}</h4><p>{{ copy.case }}: {{ member.case_label }} · {{ copy.state }}: {{ stateText(member.disposition) }}</p><p>{{ copy.seed }}: <span class="mono seed">{{ member.seed }}</span></p><p>{{ copy.cancelIntent }}: {{ member.cancel_requested ? copy.yes : copy.no }}</p>
          <p v-if="member.metrics === null" class="unavailable-metrics">{{ copy.metrics }}: {{ copy.unavailable }}</p>
          <div v-for="(metrics, platform) in member.metrics || {}" :key="platform" class="platform-metrics"><h5>{{ platform }}</h5><p>{{ copy.loggedTotal }}: <span class="logged-total">{{ metrics.logged_action_total }}</span></p>
            <details><summary>{{ copy.metricDetails }}</summary><h5>{{ copy.byType }}</h5><dl><div v-for="(count, action) in metrics.logged_action_by_type" :key="action"><dt>{{ action }}</dt><dd>{{ count }}</dd></div></dl><h5>{{ copy.tables }}</h5><dl><div v-for="(count, table) in metrics.final_table_counts" :key="table"><dt>{{ table }}</dt><dd>{{ metricText(count) }}</dd></div></dl></details>
          </div>
          <details><summary>{{ copy.provenance }}</summary><dl><dt>{{ copy.memberId }}</dt><dd class="mono">{{ member.member_id }}</dd><dt>{{ copy.run }}</dt><dd class="mono">{{ member.run_id }}</dd><dt>{{ copy.project }}</dt><dd class="mono">{{ result.project_id }}</dd><dt>{{ copy.revision }}</dt><dd>{{ member.project_revision }}</dd><dt>{{ copy.rounds }}</dt><dd>{{ member.max_rounds }}</dd><dt>{{ copy.platforms }}</dt><dd>{{ member.platforms.join(', ') }}</dd><dt>{{ copy.runtimeDigest }}</dt><dd class="mono">{{ member.runtime_sha256 }}</dd><dt>{{ copy.preparedDigest }}</dt><dd class="mono">{{ member.prepared_artifact_sha256 }}</dd><dt>{{ copy.requestDigest }}</dt><dd class="mono">{{ member.request_fingerprint }}</dd><dt>{{ copy.recordDigest }}</dt><dd class="mono">{{ member.record_digest }}</dd></dl>
            <template v-if="member.recording"><h5>{{ copy.recording }}</h5><dl><dt>{{ copy.recordingRevision }}</dt><dd class="mono">{{ member.recording.recording_revision }}</dd><div v-for="(value, key) in member.recording.anchors" :key="key"><dt>{{ copy.anchorLabels[key] }}</dt><dd class="mono">{{ value }}</dd></div></dl><h5>{{ copy.runtimeVersions }}</h5><dl><div v-for="(value, key) in member.recording.runtime_versions" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div></dl><h5>{{ copy.artifacts }}</h5><dl><div v-for="(value, key) in member.recording.artifact_sha256" :key="key"><dt>{{ key }}</dt><dd class="mono">{{ value }}</dd></div></dl></template><p v-else>{{ copy.recording }}: {{ copy.unavailable }}</p>
          </details>
        </article>
      </div>
      <details class="distributions"><summary>{{ copy.distributions }}</summary><p>{{ copy.distributionHint }}</p>
        <article v-for="(group, i) in result.distributions" :key="i"><h4>{{ group.case_label }} · {{ group.platform }}</h4><p>{{ copy.members }}: {{ group.member_count }} · {{ stateText('successful') }}: {{ group.successful_count }} · {{ copy.nonSuccessful }}: {{ group.non_successful_count }}</p><p>{{ copy.declaredSeeds }}: {{ group.distinct_declared_seed_count }} · {{ copy.successfulSeeds }}: {{ group.distinct_successful_seed_count }}</p>
          <details v-for="(series, category) in group.metrics" :key="category"><summary>{{ copy.metricLabels[category] }}</summary><div v-for="(distribution, metric) in category === 'logged_action_total' ? { total: series } : series" :key="metric" class="distribution-card"><h5>{{ metric === 'total' ? copy.loggedTotal : metric }}</h5><dl><div v-for="(value, statistic) in distribution" :key="statistic"><dt>{{ copy.statisticLabels[statistic] }}</dt><dd>{{ metricText(value) }}</dd></div></dl></div></details>
        </article>
      </details>
      <details class="comparability"><summary>{{ copy.comparability }}</summary><p>{{ copy.comparabilityHint }}</p><p v-if="!result.comparability_matrix.length">{{ copy.singleMember }}</p><details v-for="pair in result.comparability_matrix" :key="pair.left_member_id + '|' + pair.right_member_id"><summary>{{ pair.left_member_id }} / {{ pair.right_member_id }}</summary><dl><div v-for="(cell, field) in pair.fields" :key="field"><dt>{{ copy.fieldLabels[field] }}</dt><dd>{{ cell.equal === null ? copy.unavailable : cell.equal ? copy.same : copy.different }}</dd></div></dl></details></details>
      <details class="result-provenance"><summary>{{ copy.resultProvenance }}</summary><dl><dt>{{ copy.cohortDigest }}</dt><dd class="mono">{{ result.cohort_manifest_digest }}</dd><dt>{{ copy.nativeDigest }}</dt><dd class="mono">{{ result.native_result_digest }}</dd><dt>{{ copy.publicDigest }}</dt><dd class="mono">{{ result.public_projection_digest }}</dd></dl><p>{{ copy.fingerprints }}</p><p>{{ copy.coverage }}</p></details>
    </section>
  </section>
</template>
<style scoped>
.experiment-comparison{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere}.experiment-comparison *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 8px}h3{font-size:1.2rem}h4,h5{font-size:1rem;margin:8px 0}p{margin:8px 0}.section-heading{display:flex;flex-wrap:wrap;align-items:start;justify-content:space-between;gap:16px}.section-heading>div{flex:1 1 240px;min-width:0}.help{font-size:.875rem;color:var(--muted)}button,input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--control-border);border-radius:5px;min-height:44px;max-width:100%}button{padding:10px 16px;color:var(--primary);font-weight:550;cursor:pointer}button:disabled{opacity:.65;cursor:default}.primary{background:var(--primary);color:var(--surface)}input{padding:10px 12px;width:100%;min-width:0}fieldset{border:0;margin:16px 0;padding:0;min-width:0}legend{font-weight:650}.title-field{display:flex;flex-direction:column;gap:8px;margin-top:16px;font-weight:550}.member-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:16px;margin:16px 0}.catalog-member,.result-member,.distribution-card{min-width:0;border:1px solid var(--border);border-radius:6px;padding:16px}.member-choice{display:flex;align-items:center;gap:8px;min-height:44px;font-weight:650;cursor:pointer}.member-choice input{width:20px;min-height:20px;height:20px;flex-shrink:0}.feedback,.limitations{border-left:3px solid var(--primary);padding:8px 12px}.field-error{color:var(--ink);font-weight:650}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace;overflow-wrap:anywhere}.accounting,.catalog-identity{display:flex;flex-wrap:wrap;gap:16px}.accounting>div{flex:1 1 110px;background:var(--canvas);padding:12px}.accounting dd{font-size:1.5rem;font-weight:650}dt{font-weight:550}dd{margin:0 0 8px;overflow-wrap:anywhere}dl{margin:8px 0;min-width:0}summary{cursor:pointer;min-height:44px;padding:10px 0}details{min-width:0}details details{padding:0 8px;border-top:1px solid var(--border)}.comparison-result{margin-top:24px;border-top:1px solid var(--border);padding-top:16px;min-width:0}button:focus-visible,input:focus-visible,summary:focus-visible,[tabindex="-1"]:focus{outline:3px solid var(--primary);outline-offset:3px}.distributions,.comparability,.result-provenance{border-top:1px solid var(--border);margin-top:12px}.distribution-card{margin:8px 0}@media(max-width:480px){.experiment-comparison{padding:16px}.member-grid{gap:12px}.catalog-load,.compare-submit{width:100%}}
</style>
