<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { nativeLaunchIdentity, nativeReadyPreparation, newNativeLaunchId, validateNativeLaunchResult } from '../../api/nativeLaunch.js'
import { nativeLaunchCopyFor, nativeLaunchError } from '../../i18n/nativeLaunch.js'
const props = defineProps({ methods: { type: Object, required: true }, ready: { type: Object, default: null }, displayGraphId: { type: String, default: '' }, connected: Boolean, busy: Boolean, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' } })
const copy = computed(() => nativeLaunchCopyFor(props.locale))
const plan = ref(null), binding = ref(null), history = ref([]), pending = ref(false), attempted = ref(false), error = ref(''), notice = ref(''), feedback = ref(null)
const started = new Set()
let declaration = null
let lifetime = 0, reviewEpoch = 0
const locked = computed(() => !props.connected || props.busy || pending.value)
const canStart = computed(() => !locked.value && !attempted.value && plan.value?.state === 'planned' && plan.value.authorization.model_calls_enabled && !!plan.value.ceiling_microusd)
const records = computed(() => [plan.value && { key: 'plan', value: plan.value, preparation: binding.value }, ...history.value.filter(record => record.value.request.run_id !== plan.value?.request.run_id)].filter(Boolean))
function retain(value, preparation) {
  const entry = { key: 'durable', value, preparation }, index = history.value.findIndex(record => record.value.request.run_id === value.request.run_id)
  if (index < 0) history.value.push(entry); else history.value[index] = entry
}
function clear() { lifetime++; reviewEpoch++; declaration = null; started.clear(); plan.value = null; binding.value = null; history.value = []; attempted.value = false; pending.value = false; error.value = ''; notice.value = ''; props.methods.clear?.() }
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.connected, value => { if (!value) clear() }, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
watch(() => props.ready, () => { reviewEpoch++; declaration = null; if (history.value.length) notice.value = 'changed'; plan.value = null; binding.value = null; attempted.value = false; error.value = '' }, { deep: true, flush: 'sync' })
onBeforeUnmount(clear)
async function announce() { await nextTick(); feedback.value?.focus() }
async function review() {
  if (locked.value || !props.ready) return
  if (history.value.length >= 100) { error.value = 'busy'; await announce(); return }
  const life = lifetime, epoch = ++reviewEpoch
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const ready = await nativeReadyPreparation(props.ready, props.displayGraphId)
    if (life !== lifetime || epoch !== reviewEpoch) return
    declaration ||= { schema_version: 1, launch_id: newNativeLaunchId(), preparation: { operation_id: ready.operation_id, plan_sha256: ready.plan_sha256 } }
    const payload = JSON.parse(JSON.stringify(declaration))
    const value = await props.methods.plan(JSON.parse(JSON.stringify(payload)), JSON.parse(JSON.stringify(ready)))
    const admitted = await validateNativeLaunchResult(value, { graph: props.displayGraphId, payload, preparation: ready, planning: true })
    if (life !== lifetime || epoch !== reviewEpoch) return
    binding.value = ready; plan.value = admitted; declaration = null; attempted.value = started.has(admitted.request.run_id)
  } catch (e) { if (life === lifetime && epoch === reviewEpoch) { if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) clear(); error.value = e?.code || 'invalid_reply' } }
  finally { if (life === lifetime) { pending.value = false; await announce() } }
}
async function request(method, record) {
  const target = record?.value || plan.value, targetBinding = record?.preparation || binding.value
  if (locked.value || !target || method === 'start' && (!canStart.value || target.request.run_id !== plan.value?.request.run_id) || method === 'cancel' && (target.receipt || target.cancel_requested)) return
  const life = lifetime, known = JSON.parse(JSON.stringify(target)), ready = JSON.parse(JSON.stringify(targetBinding)), payload = nativeLaunchIdentity(known)
  if (method === 'start' || method === 'cancel') { if (plan.value?.request.run_id === known.request.run_id) attempted.value = true; started.add(known.request.run_id); retain(known, ready) }
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const value = await props.methods[method](JSON.parse(JSON.stringify(payload)))
    const admitted = await validateNativeLaunchResult(value, { graph: props.displayGraphId, payload, preparation: ready, known })
    if (life !== lifetime) return
    if (plan.value?.request.run_id === admitted.request.run_id) { plan.value = admitted; if (admitted.state !== 'planned') attempted.value = true }
    if (started.has(admitted.request.run_id) || admitted.state !== 'planned') { started.add(admitted.request.run_id); retain(admitted, ready) }
  } catch (e) {
    if (life === lifetime) {
      if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) { clear(); error.value = e.code }
      else { error.value = e?.code || 'transport_failure'; if (['start', 'cancel'].includes(method) && ['deadline', 'timeout', 'cancelled', 'transport_failure', 'invalid_reply', 'native_launch_uncertain'].includes(error.value)) notice.value = 'lost' }
    }
  } finally { if (life === lifetime) { pending.value = false; await announce() } }
}
</script>
<template>
  <section class="native-launch" :lang="locale" aria-labelledby="native-launch-title">
    <h2 id="native-launch-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p>
    <p v-if="!ready && !plan && !history.length">{{ copy.missing }}</p>
    <div class="actions"><button class="review" type="button" :disabled="locked || !ready" @click="review">{{ copy.review }}</button></div>
    <p class="limitations">{{ copy.distinction }}</p><p class="limitations">{{ copy.reservation }}</p><p class="limitations">{{ copy.reviewPolicy }}</p><p class="limitations">{{ copy.closing }}</p>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ nativeLaunchError(copy, error) }}</span><span v-if="notice"> {{ copy[notice] }}</span><span v-if="!pending && !error && plan"> {{ copy.states[plan.state] }}</span></p>
    <article v-for="record in records" :key="record.value.request.run_id" class="plan" :data-record="record.key">
      <h3>{{ copy.states[record.value.state] }}</h3>
      <dl><dt>{{ copy.source }}</dt><dd>{{ record.preparation.source.source_name }}<br><span class="mono">{{ record.preparation.source.source_revision }}<br>{{ record.preparation.source.source_sha256 }}</span></dd><dt>{{ copy.project }}</dt><dd class="mono">{{ record.value.request.project_id }}</dd><dt>{{ copy.revision }}</dt><dd>{{ record.value.request.project_revision }}</dd><dt>{{ copy.operation }}</dt><dd class="mono">{{ record.value.preparation.operation_id }}</dd><dt>{{ copy.simulation }}</dt><dd class="mono">{{ record.value.request.simulation_id }}</dd><dt>{{ copy.artifact }}</dt><dd class="mono">{{ record.value.request.artifact_sha256 }}</dd><dt>{{ copy.run }}</dt><dd class="mono">{{ record.value.request.run_id }}</dd><dt>{{ copy.digest }}</dt><dd class="mono">{{ record.value.launch_sha256 }}</dd><dt>{{ copy.runtime }}</dt><dd class="mono">{{ record.value.request.runtime_sha256 }}</dd><dt>{{ copy.model }}</dt><dd>{{ record.value.model_label }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ record.value.ceiling_microusd || '—' }}</dd><dt>{{ copy.platforms }}</dt><dd>{{ record.value.request.platforms.join(', ') }}</dd><dt>{{ copy.seed }}</dt><dd>{{ record.value.request.seed }}</dd><dt>{{ copy.rounds }}</dt><dd>{{ record.value.request.max_rounds }}</dd><dt>{{ copy.calls }}</dt><dd>{{ record.value.limits.max_calls }}</dd><dt>{{ copy.input }}</dt><dd>{{ record.value.limits.max_input_bytes }}</dd><dt>{{ copy.output }}</dt><dd>{{ record.value.limits.max_output_tokens }}</dd><dt>{{ copy.seconds }}</dt><dd>{{ record.value.limits.max_run_seconds }}</dd></dl>
      <p v-if="record.value.error_code">{{ nativeLaunchError(copy, record.value.error_code) }}</p>
      <p v-if="record.value.cancel_requested" class="cancellation">{{ copy.cancellation }}</p>
      <h4>{{ copy.cleanup }}</h4><p class="cleanup">{{ !record.value.cleanup.known ? copy.unknown : record.value.cleanup.pending ? copy.pendingCleanup : copy.observedCleanup }}<span v-if="record.value.cleanup.known"> · {{ record.value.cleanup.owner_thread_alive ? copy.alive : copy.stopped }}</span></p>
      <template v-if="record.value.workflow"><dl><dt>{{ copy.workflow }}</dt><dd class="mono">{{ record.value.workflow.workflow_id }}</dd><dt>{{ copy.temporal }}</dt><dd class="mono">{{ record.value.workflow.temporal_run_id }}</dd></dl></template>
      <template v-if="record.value.receipt"><h4>{{ copy.receipt }}</h4><dl class="receipt"><dt>{{ copy.attempt }}</dt><dd class="mono">{{ record.value.receipt.attempt_id }}</dd><dt>{{ copy.instance }}</dt><dd class="mono">{{ record.value.receipt.instance_id }}</dd><dt>{{ copy.requestDigest }}</dt><dd class="mono">{{ record.value.receipt.request_fingerprint }}</dd><dt>{{ copy.evidence }}</dt><dd class="mono">{{ record.value.receipt.evidence_sha256 }}</dd></dl></template><p v-else class="no-receipt">{{ copy.noReceipt }}</p>
      <p v-if="!record.value.authorization.model_calls_enabled || !record.value.ceiling_microusd" class="limitations">{{ copy.disabled }}</p>
      <div class="actions"><button v-if="record.key === 'plan'" class="start primary" type="button" :disabled="!canStart" @click="request('start', record)">{{ copy.start }}</button><button class="refresh" type="button" :disabled="locked" @click="request('status', record)">{{ copy.refresh }}</button><button class="cancel" type="button" :disabled="locked || !!record.value.receipt || record.value.cancel_requested" @click="request('cancel', record)">{{ copy.cancel }}</button></div>
    </article>
    <div class="actions"><button class="clear" type="button" @click="clear">{{ copy.clear }}</button></div>
  </section>
</template>
<style scoped>
.native-launch{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere;font:16px/1.5 system-ui,sans-serif}.native-launch *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 16px}h3{font-size:1.1rem}p{margin:8px 0}.limitations{color:var(--muted,#526174);font-size:.875rem}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}button{min-height:44px;max-width:100%;padding:10px 16px;border:1px solid var(--border,#b9c4d2);border-radius:5px;font:inherit;color:var(--primary,#174d96);background:var(--surface,#fff);cursor:pointer;overflow-wrap:anywhere;touch-action:manipulation}.primary{color:white;background:var(--primary,#174d96);border-color:var(--primary,#174d96)}button:disabled{opacity:.65;cursor:default}button:hover:not(:disabled){filter:brightness(.92)}button:active:not(:disabled){filter:brightness(.85)}:is(button,.feedback):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.feedback{border-left:3px solid var(--primary,#174d96);padding:8px 12px}.feedback:empty{display:none}.plan{border-top:1px solid var(--border,#b9c4d2);padding-top:16px;margin-top:16px}dl{display:grid;gap:4px 16px}dt{font-weight:550}dd{margin:0 0 8px;min-width:0}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}@media(min-width:768px){dl{grid-template-columns:minmax(140px,1fr) minmax(0,3fr)}}@media(max-width:480px){.native-launch{padding:16px}}
</style>
