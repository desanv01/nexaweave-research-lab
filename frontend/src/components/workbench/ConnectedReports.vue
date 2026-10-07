<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { nativeObservationSelection } from '../../api/nativeObservations.js'
import { connectedReportPayload, newReportId, reportIdentity, reportSnapshot, validateConnectedReportResult, validateConnectedReportRead, validateConnectedReportDownload } from '../../api/connectedReports.js'
import { connectedReportsCopyFor, connectedReportsError } from '../../i18n/connectedReports.js'
import { MAX_MARKDOWN_CHARS, renderSafeMarkdown } from '../../utils/safeMarkdown.js'
const props = defineProps({ methods: { type: Object, required: true }, selection: { type: Object, default: null }, connected: Boolean, busy: Boolean, displayGraphId: { type: String, default: '' }, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' } })
const copy = computed(() => connectedReportsCopyFor(props.locale))
const selected = ref(null), requirement = ref(''), outputLanguage = ref('en'), coverageMode = ref('complete'), declared = ref([])
const plan = ref(null), history = ref([]), pending = ref(false), error = ref(''), notice = ref(''), feedback = ref(null)
const recoveryId = ref(''), recoveryHash = ref(''), content = ref(null), artifact = ref('report'), sectionIndex = ref('1'), download = ref(null)
const attempted = new Set()
let epoch = 0, declaration = null, url = null
const clone = reportSnapshot
const locked = computed(() => !props.connected || props.busy || pending.value)
const records = computed(() => [plan.value && { value: plan.value, reviewed: true }, ...history.value.filter(v => v.report_id !== plan.value?.report_id).map(value => ({ value, reviewed: false }))].filter(Boolean))
const canStart = v => !locked.value && v === plan.value && v.state === 'planned' && !attempted.has(v.report_id) && v.authorization.model_calls_enabled && v.authorization.budget_configured && !!v.ceiling_microusd
const rendered = computed(() => content.value && content.value.content.length <= MAX_MARKDOWN_CHARS ? renderSafeMarkdown(content.value.content) : '')
function discardArtifacts() { content.value = null; download.value = null; if (url) { URL.revokeObjectURL(url); url = null } }
function invalidate() { epoch++; props.methods.clear?.(); pending.value = false; declaration = null; discardArtifacts() }
function clear() { invalidate(); selected.value = null; plan.value = null; history.value = []; requirement.value = ''; recoveryId.value = ''; recoveryHash.value = ''; error.value = ''; notice.value = '' }
function retain(value) { const index = history.value.findIndex(v => v.report_id === value.report_id); if (index < 0) history.value.push(value); else history.value[index] = value }
async function announce() { await nextTick(); feedback.value?.focus() }
function changeOptions() {
  invalidate()
  if (plan.value && attempted.has(plan.value.report_id)) retain(clone(plan.value))
  if (plan.value || history.value.length) notice.value = 'changed'
  plan.value = null; error.value = ''
}
async function select(value) {
  changeOptions(); selected.value = null; declared.value = []
  if (!value || !props.connected) return
  const life = epoch
  try {
    const admitted = await nativeObservationSelection(value, props.displayGraphId)
    if (life !== epoch || !props.connected) return
    selected.value = admitted; declared.value = admitted.launch.request.platforms.map(platform => ({ platform, enabled: true, offset: '0', count: '1' }))
  } catch (e) { if (life === epoch) { error.value = e?.code || 'invalid_reply'; await announce() } }
}
watch(() => props.selection, select, { deep: true, immediate: true, flush: 'sync' })
watch([requirement, outputLanguage, coverageMode, declared], changeOptions, { deep: true, flush: 'sync' })
watch(() => props.connected, value => { if (!value) clear() }, { flush: 'sync' })
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
watch([artifact, sectionIndex], () => { download.value = null; if (url) { URL.revokeObjectURL(url); url = null } }, { flush: 'sync' })
onBeforeUnmount(clear)
function windowNumber(v) { if (typeof v !== 'string' || !/^(0|[1-9][0-9]*)$/.test(v)) throw { code: 'invalid_request' }; return Number(v) }
async function review() {
  if (locked.value || !selected.value) return
  if (history.value.length >= 100) { error.value = 'busy'; await announce(); return }
  let payload
  try {
    declaration ||= newReportId()
    payload = { schema_version: 1, report_id: declaration, launch_id: selected.value.launch.request.run_id, launch_sha256: selected.value.launch.launch_sha256, requirement: requirement.value, output_language: outputLanguage.value, native_windows: coverageMode.value === 'complete' ? null : declared.value.filter(w => w.enabled).map(w => ({ platform: w.platform, offset: windowNumber(w.offset), count: windowNumber(w.count) })) }
    connectedReportPayload(payload, 'plan')
  } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  const life = epoch, selection = clone(selected.value)
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.plan(clone(payload), clone(selection))
    const value = await validateConnectedReportResult(reply, { graph: props.displayGraphId, payload, selection, planning: true })
    if (life !== epoch) return
    plan.value = value; declaration = null; recoveryId.value = value.report_id; recoveryHash.value = value.plan_sha256; notice.value = 'reviewed'
  } catch (e) { handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
function handleError(e, life, method = '') {
  if (life !== epoch) return
  const code = e?.code || 'invalid_reply'
  if (['unauthorized', 'origin_denied', 'disconnected', 'tombstoned'].includes(code)) { clear(); error.value = code; void announce(); return }
  error.value = code
  if (['start', 'cancel'].includes(method) && e?.replyConfirmed !== true && e?.requestSent !== false) notice.value = 'lost'
}
async function request(method, target) {
  if (locked.value || !target || method === 'start' && !canStart(target) || method === 'cancel' && (target.receipt || target.cancel_requested)) return
  const known = clone(target), payload = reportIdentity(known), life = epoch
  if (method === 'start' || method === 'cancel') { attempted.add(known.report_id); retain(known) }
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  recoveryId.value = known.report_id; recoveryHash.value = known.plan_sha256
  try {
    const reply = await props.methods[method](clone(payload), clone(known))
    const context = { graph: props.displayGraphId, payload, known }
    const value = method === 'read' ? await validateConnectedReportRead(reply, context) : await validateConnectedReportResult(reply, context)
    if (life !== epoch) return
    const report = method === 'read' ? value.report : value
    if (plan.value?.report_id === report.report_id) plan.value = report
    if (attempted.has(report.report_id) || report.state !== 'planned' || method === 'cancel') { attempted.add(report.report_id); retain(report) }
    if (method === 'read') { content.value = value; notice.value = 'readReady' }
  } catch (e) { handleError(e, life, method) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function recover() {
  if (locked.value) return
  const payload = { schema_version: 1, report_id: recoveryId.value, plan_sha256: recoveryHash.value }
  try { connectedReportPayload(payload) } catch (e) { error.value = e.code; await announce(); return }
  const life = epoch, known = records.value.find(r => r.value.report_id === payload.report_id)?.value
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.status(clone(payload), known ? clone(known) : null)
    const value = await validateConnectedReportResult(reply, { graph: props.displayGraphId, payload, known: known ? clone(known) : null })
    if (life !== epoch) return
    attempted.add(value.report_id); retain(value); if (plan.value?.report_id === value.report_id) plan.value = null
    notice.value = 'recovered'
  } catch (e) { handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function prepareDownload(target) {
  if (locked.value || target.state !== 'completed') return
  let payload
  try { payload = { ...reportIdentity(target), kind: artifact.value, section_index: artifact.value === 'section' ? windowNumber(sectionIndex.value) : null }; connectedReportPayload(payload, 'download') } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  const life = epoch, known = clone(target)
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.download(clone(payload), clone(known))
    const value = await validateConnectedReportDownload(reply, { graph: props.displayGraphId, payload, known })
    if (life !== epoch) return
    const bytes = Uint8Array.from(atob(value.artifact.content_base64), c => c.charCodeAt(0))
    url = URL.createObjectURL(new Blob([bytes], { type: value.artifact.mime }))
    download.value = { url, name: value.artifact.name, literal: value.artifact.mime === 'application/json' ? new TextDecoder('utf-8', { fatal: true }).decode(bytes) : null, report_id: known.report_id }
    notice.value = 'downloadReady'
  } catch (e) { if (life === epoch) discardArtifacts(); handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
</script>
<template>
  <section class="connected-reports" :lang="locale" aria-labelledby="connected-reports-title" :aria-busy="pending">
    <h2 id="connected-reports-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p><p v-if="!selected">{{ copy.missing }}</p>
    <form @submit.prevent="review"><fieldset :disabled="locked || !selected">
      <label for="report-requirement">{{ copy.requirement }}<textarea id="report-requirement" v-model="requirement" rows="3" required aria-describedby="report-context-help report-feedback"></textarea></label>
      <div class="options"><label for="report-output-language">{{ copy.outputLanguage }}<select id="report-output-language" v-model="outputLanguage"><option value="en">English</option><option value="zh">中文</option><option value="ms">Bahasa Melayu</option></select></label><label for="report-coverage">{{ copy.coverageMode }}<select id="report-coverage" v-model="coverageMode"><option value="complete">{{ copy.complete }}</option><option value="selected">{{ copy.selected }}</option></select></label></div>
      <div v-if="coverageMode === 'selected'" class="windows"><div v-for="w in declared" :key="w.platform" class="window"><label class="check"><input v-model="w.enabled" type="checkbox">{{ w.platform }}</label><label :for="`report-offset-${w.platform}`">{{ copy.offset }}<input :id="`report-offset-${w.platform}`" v-model="w.offset" :disabled="!w.enabled" inputmode="numeric" autocomplete="off" spellcheck="false"></label><label :for="`report-count-${w.platform}`">{{ copy.count }}<input :id="`report-count-${w.platform}`" v-model="w.count" :disabled="!w.enabled" inputmode="numeric" autocomplete="off" spellcheck="false"></label></div><p>{{ copy.windowHint }}</p></div>
      <div class="actions"><button class="report-review primary" type="submit">{{ copy.review }}</button></div>
    </fieldset></form>
    <p id="report-context-help" class="limitations">{{ copy.snapshot }}</p><p class="limitations">{{ copy.reservation }}</p><p class="limitations">{{ copy.closing }}</p>
    <p id="report-feedback" ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ connectedReportsError(copy, error) }}</span><span v-if="notice"> {{ copy[notice] }}</span></p>
    <form class="recovery" @submit.prevent="recover"><h3>{{ copy.recovery }}</h3><p id="report-recovery-help">{{ copy.recoveryHint }}</p><fieldset :disabled="locked"><label for="report-recovery-id">{{ copy.reportId }}<input id="report-recovery-id" v-model="recoveryId" required maxlength="36" autocomplete="off" spellcheck="false" aria-describedby="report-recovery-help report-feedback"></label><label for="report-recovery-hash">{{ copy.planHash }}<input id="report-recovery-hash" v-model="recoveryHash" required maxlength="64" autocomplete="off" spellcheck="false" aria-describedby="report-recovery-help report-feedback"></label><button class="report-recover" type="submit">{{ copy.recover }}</button></fieldset></form>
    <article v-for="record in records" :key="record.value.report_id" class="report-record" :data-report-id="record.value.report_id">
      <h3>{{ copy.states[record.value.state] }}</h3>
      <dl><dt>{{ copy.reportId }}</dt><dd class="mono">{{ record.value.report_id }}</dd><dt>{{ copy.planHash }}</dt><dd class="mono">{{ record.value.plan_sha256 }}</dd><dt>{{ copy.requirement }}</dt><dd>{{ record.value.options.requirement }}</dd><dt>{{ copy.outputLanguage }}</dt><dd>{{ record.value.options.output_language }}</dd><dt>{{ copy.source }}</dt><dd>{{ record.value.binding.source.source_name }}<br><span class="mono">{{ record.value.binding.source.source_revision }}<br>{{ record.value.binding.source.source_sha256 }}</span></dd><dt>{{ copy.native }}</dt><dd class="mono">{{ record.value.binding.native.run_id }}</dd><dt>{{ copy.nativeHash }}</dt><dd class="mono">{{ record.value.binding.native.evidence_sha256 }}</dd><dt>{{ copy.contextHash }}</dt><dd class="mono">{{ record.value.context_sha256 }}</dd><dt>{{ copy.projectionHash }}</dt><dd class="mono">{{ record.value.source_projection_sha256 }}</dd><dt>{{ copy.model }}</dt><dd>{{ record.value.model_label }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ record.value.ceiling_microusd ?? copy.unknown }}</dd><dt>{{ copy.calls }}</dt><dd>{{ record.value.limits.max_calls }}</dd><dt>{{ copy.input }}</dt><dd>{{ record.value.limits.max_input_bytes }}</dd><dt>{{ copy.output }}</dt><dd>{{ record.value.limits.max_output_tokens }}</dd><dt>{{ copy.seconds }}</dt><dd>{{ record.value.limits.max_run_seconds }}</dd><dt>{{ copy.progress }}</dt><dd>{{ copy.stages[record.value.progress.stage] }} · {{ record.value.progress.percent }}% · {{ copy.sections }}: {{ record.value.progress.completed_sections }} / {{ record.value.progress.total_sections }}</dd></dl>
      <h4>{{ copy.coverageMode }}</h4><p>{{ record.value.options.native_windows === null ? copy.fullCoverage : copy.partialCoverage }}</p><ul class="coverage"><li v-for="c in record.value.binding.coverage" :key="c.platform">{{ c.platform }} · {{ copy.records }}: {{ c.selected_records }} / {{ c.total_records }} · {{ c.complete ? copy.complete : copy.partialCoverage }}<span v-for="w in c.windows" :key="w.offset"> · {{ copy.windows }}: {{ w.offset }} + {{ w.count }}</span></li></ul>
      <details class="references"><summary>{{ copy.references }}</summary><h4>{{ copy.sourceReferences }}</h4><ul><li v-for="key in record.value.binding.reference_keys.filter(k => k.startsWith('source:'))" :key="key" class="mono">{{ key }}</li></ul><h4>{{ copy.nativeReferences }}</h4><ul><li v-for="key in record.value.binding.reference_keys.filter(k => k.startsWith('native:'))" :key="key" class="mono">{{ key }}</li></ul></details>
      <p v-if="record.value.error_code">{{ connectedReportsError(copy, record.value.error_code) }}</p><p v-if="record.value.cancel_requested">{{ copy.cancellation }}</p>
      <h4>{{ copy.cleanup }}</h4><p class="cleanup">{{ record.value.cleanup.known ? (record.value.cleanup.pending ? copy.pendingCleanup : copy.observedCleanup) : copy.unknown }} · {{ record.value.cleanup.owner_thread_alive === null ? copy.unknown : record.value.cleanup.owner_thread_alive ? copy.alive : copy.stopped }}</p>
      <template v-if="record.value.receipt"><h4>{{ copy.receipt }}</h4><dl><dt>{{ copy.receiptHash }}</dt><dd class="mono">{{ record.value.receipt_sha256 }}</dd><dt>{{ copy.manifestHash }}</dt><dd class="mono">{{ record.value.receipt.manifest_sha256 }}</dd></dl><p class="limitations">{{ copy.interpretation }}</p></template><p v-else>{{ copy.noReceipt }}</p>
      <p v-if="!record.value.authorization.model_calls_enabled || !record.value.authorization.budget_configured || !record.value.ceiling_microusd">{{ copy.disabled }}</p>
      <div class="actions"><button v-if="record.reviewed && record.value.state === 'planned'" class="report-start primary" type="button" :disabled="!canStart(record.value)" @click="request('start', record.value)">{{ copy.start }}</button><button class="report-refresh" type="button" :disabled="locked" @click="request('status', record.value)">{{ copy.refresh }}</button><button class="report-cancel" type="button" :disabled="locked || !!record.value.receipt || record.value.cancel_requested" @click="request('cancel', record.value)">{{ copy.cancel }}</button><button v-if="record.value.state === 'completed'" class="report-read" type="button" :disabled="locked" @click="request('read', record.value)">{{ copy.read }}</button></div>
      <div v-if="record.value.state === 'completed'" class="download-controls"><label :for="`report-artifact-${record.value.report_id}`">{{ copy.artifact }}<select :id="`report-artifact-${record.value.report_id}`" v-model="artifact" :disabled="locked"><option v-for="(label, kind) in copy.artifacts" :key="kind" :value="kind">{{ label }}</option></select></label><label v-if="artifact === 'section'" :for="`report-section-${record.value.report_id}`">{{ copy.section }}<select :id="`report-section-${record.value.report_id}`" v-model="sectionIndex" :disabled="locked"><option v-for="n in record.value.progress.total_sections" :key="n" :value="String(n)">{{ n }}</option></select></label><button class="report-download" type="button" :disabled="locked" @click="prepareDownload(record.value)">{{ copy.download }}</button><a v-if="download?.report_id === record.value.report_id" class="report-save" :href="download.url" :download="download.name">{{ copy.save }} · {{ download.name }}</a></div>
    </article>
    <section v-if="content" class="narrative" aria-labelledby="report-narrative-title"><h3 id="report-narrative-title">{{ copy.narrative }}</h3><p class="mono">{{ content.report.report_id }}</p><p>{{ copy.interpretation }}</p><div v-if="rendered" class="markdown" v-html="rendered"></div><template v-else><p v-if="content.content.length > MAX_MARKDOWN_CHARS">{{ copy.largeContent }}</p><pre class="literal">{{ content.content }}</pre></template></section>
    <details v-if="download?.literal" class="literal-artifact"><summary>{{ copy.literal }} · {{ download.name }}</summary><pre class="literal">{{ download.literal }}</pre></details>
    <div class="actions"><button class="report-clear" type="button" @click="clear">{{ copy.clear }}</button></div>
  </section>
</template>
<style scoped>
.connected-reports{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere;font:16px/1.5 system-ui,sans-serif}.connected-reports *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 16px}h3{font-size:1.1rem}h4{font-size:1rem}p{margin:8px 0}fieldset{border:0;margin:0;padding:0;min-width:0;display:grid;gap:12px}label{display:flex;flex-direction:column;gap:6px;min-width:0;font-weight:550}input,textarea,select{width:100%;min-width:0;min-height:44px;padding:10px 12px;border:1px solid var(--control-border,#7b8797);border-radius:5px;color:inherit;background:var(--surface,#fff);font:inherit}textarea{resize:vertical}.options,.window,.download-controls{display:grid;gap:12px;margin-top:12px}.check{flex-direction:row;align-items:center;min-height:44px}.check input{width:20px;min-height:20px}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}button,.report-save{display:inline-flex;align-items:center;justify-content:center;min-height:44px;max-width:100%;padding:10px 16px;border:1px solid var(--border,#b9c4d2);border-radius:5px;font:inherit;color:var(--primary,#174d96);background:var(--surface,#fff);cursor:pointer;overflow-wrap:anywhere;touch-action:manipulation}.primary{color:white;background:var(--primary,#174d96);border-color:var(--primary,#174d96)}button:disabled,fieldset:disabled{opacity:.65}button:disabled{cursor:default}button:hover:not(:disabled),.report-save:hover{filter:brightness(.92)}:is(button,input,textarea,select,a,summary,.feedback):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.feedback{border-left:3px solid var(--primary,#174d96);padding:8px 12px}.feedback:empty{display:none}.limitations{color:var(--muted,#526174);font-size:.875rem}.report-record,.recovery,.narrative{border-top:1px solid var(--border,#b9c4d2);margin-top:20px;padding-top:16px;min-width:0}dl{display:grid;gap:4px 16px}dt{font-weight:550}dd{margin:0 0 8px;min-width:0}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}.literal{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:.875rem/1.6 ui-monospace,Consolas,monospace;max-width:100%;padding:12px;background:var(--canvas,#f4f6f9)}summary{min-height:44px;cursor:pointer;padding:10px 0}ul{padding-left:20px}.markdown{max-width:78ch;line-height:1.7;min-width:0}.markdown :deep(pre){white-space:pre-wrap;overflow-wrap:anywhere}.markdown :deep(table){display:block;overflow-x:auto;max-width:100%}.markdown :deep(img){max-width:100%}.markdown :deep(h2),.markdown :deep(h3){line-height:1.3;margin-top:24px}.markdown :deep(a){color:var(--primary,#174d96);overflow-wrap:anywhere}@media(min-width:768px){dl{grid-template-columns:minmax(140px,1fr) minmax(0,3fr)}.options{grid-template-columns:repeat(2,minmax(0,1fr))}.window{grid-template-columns:repeat(3,minmax(0,1fr))}.download-controls{grid-template-columns:repeat(2,minmax(0,1fr));align-items:end}}@media(max-width:480px){.connected-reports{padding:16px}}@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
