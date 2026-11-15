<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { reportIdentity, validateConnectedReportResult } from '../../api/connectedReports.js'
import { connectedFollowupPayload, followupIdentity, followupSnapshot, newFollowupId, validateConnectedFollowupResult, validateConnectedFollowupRead, validateConnectedFollowupDownload, validateConnectedFollowupHistory } from '../../api/connectedFollowups.js'
import { connectedFollowupsCopyFor, connectedFollowupsError } from '../../i18n/connectedFollowups.js'
import { MAX_MARKDOWN_CHARS, renderSafeMarkdown } from '../../utils/safeMarkdown.js'

const props = defineProps({ methods: { type: Object, required: true }, selection: { type: Object, default: null }, connected: Boolean, busy: Boolean, displayGraphId: { type: String, default: '' }, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' } })
const copy = computed(() => connectedFollowupsCopyFor(props.locale))
const selected = ref(null), question = ref(''), outputLanguage = ref('en')
const plan = ref(null), records = ref([]), page = ref(null), pending = ref(false), error = ref(''), notice = ref(''), feedback = ref(null)
const recoveryId = ref(''), recoveryHash = ref(''), content = ref(null), artifact = ref('answer'), download = ref(null)
const attempted = new Set()
let epoch = 0, declaration = null, url = null
const clone = followupSnapshot
const locked = computed(() => !props.connected || props.busy || pending.value)
const shown = computed(() => [plan.value && { value: plan.value, reviewed: true }, ...records.value.filter(v => v.turn_id !== plan.value?.turn_id).map(value => ({ value, reviewed: false }))].filter(Boolean))
const canStart = v => !locked.value && v === plan.value && v.state === 'planned' && v.history.total_completed < 1000 && !attempted.has(v.turn_id) && v.authorization.model_calls_enabled && v.authorization.budget_configured && !!v.ceiling_microusd
const rendered = computed(() => content.value && content.value.content.length <= MAX_MARKDOWN_CHARS ? renderSafeMarkdown(content.value.content) : '')
function discardArtifacts() { content.value = null; download.value = null; if (url) { URL.revokeObjectURL(url); url = null } }
function invalidate() { epoch++; props.methods.clear?.(); pending.value = false; declaration = null; discardArtifacts() }
function clear() { invalidate(); selected.value = null; plan.value = null; records.value = []; page.value = null; question.value = ''; recoveryId.value = ''; recoveryHash.value = ''; error.value = ''; notice.value = '' }
function retain(value) { const index = records.value.findIndex(v => v.turn_id === value.turn_id); if (index < 0) records.value.push(value); else records.value[index] = value }
async function announce() { await nextTick(); feedback.value?.focus() }
function changeQuestion() { invalidate(); if (plan.value && attempted.has(plan.value.turn_id)) retain(clone(plan.value)); if (plan.value || records.value.length) notice.value = 'changed'; plan.value = null; error.value = '' }
async function select(value) {
  changeQuestion(); selected.value = null; page.value = null
  if (!value || !props.connected) return
  const life = epoch
  try {
    const admitted = await validateConnectedReportResult(value, { graph: props.displayGraphId, payload: reportIdentity(value), known: value })
    if (life !== epoch || !props.connected) return
    if (admitted.state !== 'completed') throw { code: 'invalid_request' }
    selected.value = admitted
  } catch (e) { if (life === epoch) { error.value = e?.code || 'invalid_reply'; await announce() } }
}
watch(() => props.selection, select, { deep: true, immediate: true, flush: 'sync' })
watch([question, outputLanguage], changeQuestion, { flush: 'sync' })
watch(() => props.connected, value => { if (!value) clear() }, { flush: 'sync' })
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
watch(artifact, () => { download.value = null; if (url) { URL.revokeObjectURL(url); url = null } }, { flush: 'sync' })
onBeforeUnmount(clear)
function handleError(e, life, method = '') {
  if (life !== epoch) return
  const code = e?.code || 'invalid_reply'
  if (['unauthorized', 'origin_denied', 'disconnected', 'tombstoned'].includes(code)) { clear(); error.value = code; void announce(); return }
  error.value = code
  if (code === 'history_changed') { if (plan.value && attempted.has(plan.value.turn_id)) retain(clone(plan.value)); plan.value = null; declaration = null; notice.value = 'historyChanged' }
  else if (['start', 'cancel'].includes(method) && e?.replyConfirmed !== true && e?.requestSent !== false) notice.value = 'lost'
}
async function review() {
  if (locked.value || !selected.value) return
  if (records.value.length >= 100) { error.value = 'busy'; await announce(); return }
  let payload
  try {
    declaration ||= newFollowupId()
    payload = { schema_version: 1, turn_id: declaration, report_id: selected.value.report_id, report_plan_sha256: selected.value.plan_sha256, question: question.value, output_language: outputLanguage.value, expected_history_sha256: page.value?.before_ordinal === null && page.value.report_id === selected.value.report_id ? page.value.head_sha256 : null }
    connectedFollowupPayload(payload, 'plan')
  } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  const life = epoch, parent = clone(selected.value)
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.plan(clone(payload), clone(parent))
    const value = await validateConnectedFollowupResult(reply, { graph: props.displayGraphId, payload, parentReport: parent, planning: true })
    if (life !== epoch) return
    plan.value = value; declaration = null; recoveryId.value = value.turn_id; recoveryHash.value = value.plan_sha256; notice.value = 'reviewed'
  } catch (e) { handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function request(method, target) {
  if (locked.value || !target || method === 'start' && !canStart(target) || method === 'cancel' && (target.receipt || target.cancel_requested)) return
  const known = clone(target), payload = followupIdentity(known), life = epoch
  if (method === 'start' || method === 'cancel') { attempted.add(known.turn_id); retain(known) }
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts(); recoveryId.value = known.turn_id; recoveryHash.value = known.plan_sha256
  try {
    const reply = await props.methods[method](clone(payload), clone(known))
    const context = { graph: props.displayGraphId, payload, known }
    const value = method === 'read' ? await validateConnectedFollowupRead(reply, context) : await validateConnectedFollowupResult(reply, context)
    if (life !== epoch) return
    const turn = method === 'read' ? value.turn : value
    if (plan.value?.turn_id === turn.turn_id) plan.value = turn
    if (attempted.has(turn.turn_id) || turn.state !== 'planned' || method === 'cancel') { attempted.add(turn.turn_id); retain(turn) }
    if (method === 'read') { content.value = value; notice.value = 'readReady' }
  } catch (e) { handleError(e, life, method) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function recover() {
  if (locked.value) return
  const payload = { schema_version: 1, turn_id: recoveryId.value, plan_sha256: recoveryHash.value }
  try { connectedFollowupPayload(payload) } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  const known = shown.value.find(r => r.value.turn_id === payload.turn_id)?.value, life = epoch
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.status(clone(payload), known ? clone(known) : null)
    const value = await validateConnectedFollowupResult(reply, { graph: props.displayGraphId, payload, known: known ? clone(known) : null })
    if (life !== epoch) return
    attempted.add(value.turn_id); retain(value); if (plan.value?.turn_id === value.turn_id) plan.value = null; notice.value = 'recovered'
  } catch (e) { handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function loadHistory(beforeOrdinal = null) {
  if (locked.value) return
  const report = selected.value || shown.value[0]?.value?.binding?.report
  if (!report) { error.value = 'invalid_request'; await announce(); return }
  const payload = { schema_version: 1, report_id: report.report_id, report_plan_sha256: report.plan_sha256, before_ordinal: beforeOrdinal }
  try { connectedFollowupPayload(payload, 'history') } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  const life = epoch
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const reply = await props.methods.history(clone(payload))
    const value = await validateConnectedFollowupHistory(reply, { graph: props.displayGraphId, payload, parentReport: selected.value ? clone(selected.value) : null })
    if (life !== epoch) return
    page.value = value
  } catch (e) { handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
async function prepareDownload(target) {
  if (locked.value || target.state !== 'completed') return
  const payload = { ...followupIdentity(target), kind: artifact.value }, known = clone(target), life = epoch
  try { connectedFollowupPayload(payload, 'download') } catch (e) { error.value = e?.code || 'invalid_request'; await announce(); return }
  pending.value = true; error.value = ''; notice.value = ''; discardArtifacts()
  try {
    const reply = await props.methods.download(clone(payload), clone(known))
    const value = await validateConnectedFollowupDownload(reply, { graph: props.displayGraphId, payload, known })
    if (life !== epoch) return
    const bytes = Uint8Array.from(atob(value.artifact.content_base64), c => c.charCodeAt(0))
    url = URL.createObjectURL(new Blob([bytes], { type: value.artifact.mime }))
    download.value = { url, name: value.artifact.name, literal: value.artifact.mime === 'application/json' ? new TextDecoder('utf-8', { fatal: true }).decode(bytes) : null, turn_id: known.turn_id }
    notice.value = 'downloadReady'
  } catch (e) { if (life === epoch) discardArtifacts(); handleError(e, life) }
  finally { if (life === epoch) { pending.value = false; await announce() } }
}
</script>
<template>
  <section class="connected-followup" :lang="locale" aria-labelledby="followup-title" :aria-busy="pending">
    <h2 id="followup-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p><p v-if="!selected">{{ copy.missing }}</p>
    <form @submit.prevent="review"><fieldset :disabled="locked || !selected">
      <label for="followup-question">{{ copy.question }}<textarea id="followup-question" v-model="question" rows="3" required maxlength="4000" aria-describedby="followup-limits followup-feedback"></textarea></label>
      <label for="followup-language">{{ copy.language }}<select id="followup-language" v-model="outputLanguage"><option value="en">English</option><option value="zh">中文</option><option value="ms">Bahasa Melayu</option></select></label>
      <button class="followup-review primary" type="submit">{{ copy.review }}</button>
    </fieldset></form>
    <div id="followup-limits" class="limitations"><p>{{ copy.historyLimit }}</p><p>{{ copy.reportLimit }}</p><p>{{ copy.exportLimit }}</p><p>{{ copy.proofNote }}</p></div>
    <p id="followup-feedback" ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ connectedFollowupsError(copy, error) }}</span><span v-if="notice"> {{ copy[notice] }}</span></p>
    <form class="recovery" @submit.prevent="recover"><h3>{{ copy.recovery }}</h3><p id="followup-recovery-help">{{ copy.recoveryHint }}</p><fieldset :disabled="locked"><label for="followup-recovery-id">{{ copy.turnId }}<input id="followup-recovery-id" v-model="recoveryId" maxlength="36" required autocomplete="off" spellcheck="false" aria-describedby="followup-recovery-help followup-feedback"></label><label for="followup-recovery-hash">{{ copy.planHash }}<input id="followup-recovery-hash" v-model="recoveryHash" maxlength="64" required autocomplete="off" spellcheck="false" aria-describedby="followup-recovery-help followup-feedback"></label><button type="submit">{{ copy.recover }}</button></fieldset></form>
    <section class="history" aria-labelledby="followup-history-title"><h3 id="followup-history-title">{{ copy.fullHistory }}</h3><div class="actions"><button type="button" :disabled="locked" @click="loadHistory(null)">{{ copy.latest }}</button><button v-if="page?.pairs?.[0]?.ordinal > 1" type="button" :disabled="locked" @click="loadHistory(page.pairs[0].ordinal)">{{ copy.older }}</button></div><template v-if="page"><p>{{ copy.total }}: {{ page.total_completed }}</p><p class="mono">{{ copy.currentHead }}: {{ page.head_sha256 }}</p><p v-if="!page.pairs.length">{{ copy.noHistory }}</p><ol v-else :start="page.pairs[0].ordinal"><li v-for="pair in page.pairs" :key="pair.turn_id"><strong>{{ copy.pair }} {{ pair.ordinal }}</strong><p>{{ pair.question }}</p><p>{{ copy.answerPrefix }}: {{ pair.answer_prefix }}</p><p v-if="pair.truncated">{{ copy.historyLimit }}</p><p class="mono">{{ pair.published_head_sha256 }}</p></li></ol></template></section>
    <article v-for="record in shown" :key="record.value.turn_id" class="turn-record" :data-turn-id="record.value.turn_id">
      <h3>{{ copy.states[record.value.state] }}</h3><dl><dt>{{ copy.turnId }}</dt><dd class="mono">{{ record.value.turn_id }}</dd><dt>{{ copy.planHash }}</dt><dd class="mono">{{ record.value.plan_sha256 }}</dd><dt>{{ copy.report }}</dt><dd class="mono">{{ record.value.binding.report.report_id }}</dd><dt>{{ copy.reportHash }}</dt><dd class="mono">{{ record.value.binding.report.receipt_sha256 }}</dd><dt>{{ copy.question }}</dt><dd>{{ record.value.options.question }}</dd><dt>{{ copy.historyHead }}</dt><dd class="mono">{{ record.value.history.head_sha256 }}</dd><dt>{{ copy.total }}</dt><dd>{{ record.value.history.total_completed }}</dd><dt>{{ copy.model }}</dt><dd>{{ record.value.model_label }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ record.value.ceiling_microusd ?? copy.unknown }}</dd><dt>{{ copy.calls }}</dt><dd>{{ record.value.limits.max_calls }}</dd><dt>{{ copy.input }}</dt><dd>{{ record.value.limits.max_input_bytes }}</dd><dt>{{ copy.output }}</dt><dd>{{ record.value.limits.max_output_tokens }}</dd><dt>{{ copy.seconds }}</dt><dd>{{ record.value.limits.max_run_seconds }}</dd><dt>{{ copy.progress }}</dt><dd>{{ copy.stages[record.value.progress.stage] }} · {{ record.value.progress.percent }}%</dd></dl>
      <p v-if="record.value.error_code">{{ connectedFollowupsError(copy, record.value.error_code) }}</p><p v-if="record.value.cancel_requested">{{ copy.cancellation }}</p>
      <h4>{{ copy.cleanup }}</h4><p>{{ record.value.cleanup.known ? (record.value.cleanup.pending ? copy.pendingCleanup : copy.observedCleanup) : copy.unknown }} · {{ record.value.cleanup.owner_thread_alive === null ? copy.unknown : record.value.cleanup.owner_thread_alive ? copy.alive : copy.stopped }}</p>
      <p v-if="record.value.receipt" class="mono">{{ copy.ordinal }}: {{ record.value.receipt.ordinal }} · {{ record.value.receipt_sha256 }} · {{ record.value.published_history_head_sha256 }}</p><p v-else>{{ copy.noReceipt }}</p>
      <details><summary>{{ copy.promptHistory }}</summary><ol :start="record.value.history.window_start"><li v-for="pair in record.value.history.pairs" :key="pair.turn_id"><p>{{ pair.question }}</p><p>{{ copy.answerPrefix }}: {{ pair.answer_prefix }}</p><p v-if="pair.truncated">{{ copy.historyLimit }}</p></li></ol><p>{{ copy.historyLimit }}</p><p>{{ copy.reportLimit }} {{ record.value.report_context.prefix_characters }} / {{ record.value.report_context.total_characters }}</p></details>
      <details><summary>{{ copy.source }} / {{ copy.native }}</summary><ul><li v-for="key in record.value.binding.native_binding.reference_keys" :key="key" class="mono">{{ key }}</li></ul></details>
      <p v-if="!record.value.authorization.model_calls_enabled || !record.value.authorization.budget_configured || !record.value.ceiling_microusd">{{ copy.disabled }}</p><p v-if="record.value.history.total_completed >= 1000">{{ copy.limitReached }}</p>
      <div class="actions"><button v-if="record.reviewed && record.value.state === 'planned'" class="followup-start primary" type="button" :disabled="!canStart(record.value)" @click="request('start', record.value)">{{ copy.start }}</button><button class="followup-refresh" type="button" :disabled="locked" @click="request('status', record.value)">{{ copy.refresh }}</button><button class="followup-cancel" type="button" :disabled="locked || !!record.value.receipt || record.value.cancel_requested" @click="request('cancel', record.value)">{{ copy.cancel }}</button><button v-if="record.value.state === 'completed'" class="followup-read" type="button" :disabled="locked" @click="request('read', record.value)">{{ copy.read }}</button></div>
      <div v-if="record.value.state === 'completed'" class="download-controls"><label :for="`followup-artifact-${record.value.turn_id}`">{{ copy.artifact }}<select :id="`followup-artifact-${record.value.turn_id}`" v-model="artifact" :disabled="locked"><option v-for="(label, kind) in copy.artifacts" :key="kind" :value="kind">{{ label }}</option></select></label><button class="followup-download" type="button" :disabled="locked" @click="prepareDownload(record.value)">{{ copy.download }}</button><a v-if="download?.turn_id === record.value.turn_id" class="followup-save" :href="download.url" :download="download.name">{{ copy.save }} · {{ download.name }}</a></div>
    </article>
    <section v-if="content" class="answer" aria-labelledby="followup-answer-title"><h3 id="followup-answer-title">{{ copy.answer }}</h3><p class="mono">{{ content.turn.turn_id }}</p><p>{{ copy.interpretation }}</p><div v-if="rendered" class="markdown" v-html="rendered"></div><template v-else><p v-if="content.content.length > MAX_MARKDOWN_CHARS">{{ copy.largeContent }}</p><pre class="literal">{{ content.content }}</pre></template></section>
    <details v-if="download?.literal" class="literal-artifact"><summary>{{ copy.literal }} · {{ download.name }}</summary><pre class="literal">{{ download.literal }}</pre></details>
    <div class="actions"><button class="followup-clear" type="button" @click="clear">{{ copy.clear }}</button></div>
  </section>
</template>
<style scoped>
.connected-followup{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere;font:16px/1.5 system-ui,sans-serif}.connected-followup *{box-sizing:border-box}h2{font-size:1.25rem}h3{font-size:1.1rem}h4{font-size:1rem}p{margin:8px 0}fieldset{border:0;margin:0;padding:0;min-width:0;display:grid;gap:12px}label{display:flex;flex-direction:column;gap:6px;min-width:0;font-weight:550}input,textarea,select{width:100%;min-width:0;min-height:44px;padding:10px 12px;border:1px solid var(--control-border,#7b8797);border-radius:5px;color:inherit;background:var(--surface,#fff);font:inherit}textarea{resize:vertical}.actions,.download-controls{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}button,.followup-save{display:inline-flex;align-items:center;justify-content:center;min-height:44px;max-width:100%;padding:10px 16px;border:1px solid var(--border,#b9c4d2);border-radius:5px;font:inherit;color:var(--primary,#174d96);background:var(--surface,#fff);cursor:pointer;overflow-wrap:anywhere;touch-action:manipulation}.primary{color:white;background:var(--primary,#174d96);border-color:var(--primary,#174d96)}button:disabled,fieldset:disabled{opacity:.65}button:disabled{cursor:default}:is(button,input,textarea,select,a,summary,.feedback):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.feedback{border-left:3px solid var(--primary,#174d96);padding:8px 12px}.feedback:empty{display:none}.limitations{color:var(--muted,#526174);font-size:.875rem}.recovery,.history,.turn-record,.answer{border-top:1px solid var(--border,#b9c4d2);margin-top:20px;padding-top:16px;min-width:0}dl{display:grid;gap:4px 16px}dt{font-weight:550}dd{margin:0 0 8px;min-width:0}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}.literal{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font:.875rem/1.6 ui-monospace,Consolas,monospace;max-width:100%;padding:12px;background:var(--canvas,#f4f6f9)}summary{min-height:44px;cursor:pointer;padding:10px 0}.markdown{max-width:78ch;line-height:1.7;min-width:0}.markdown :deep(pre){white-space:pre-wrap;overflow-wrap:anywhere}.markdown :deep(table){display:block;overflow-x:auto;max-width:100%}.markdown :deep(a){color:var(--primary,#174d96);overflow-wrap:anywhere}@media(min-width:768px){dl{grid-template-columns:minmax(140px,1fr) minmax(0,3fr)}.download-controls{align-items:end}}@media(max-width:480px){.connected-followup{padding:16px}}@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
