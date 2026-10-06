<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { nativeObservationSelection, nativeObservationsPayload, parseObservationRecord, validateNativeObservationsResult } from '../../api/nativeObservations.js'
import { nativeObservationsCopyFor, nativeObservationsError } from '../../i18n/nativeObservations.js'
const props = defineProps({ methods: { type: Object, required: true }, selection: { type: Object, default: null }, connected: Boolean, busy: Boolean, displayGraphId: { type: String, default: '' }, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' } })
const copy = computed(() => nativeObservationsCopyFor(props.locale))
const selected = ref(null), platform = ref(''), page = ref(null), pending = ref(false), error = ref(''), stale = ref(false), feedback = ref(null)
let epoch = 0
const locked = computed(() => !props.connected || props.busy || pending.value || !selected.value)
const labels = computed(() => page.value?.records.map(record => { const parsed = parseObservationRecord(record.raw_json); return typeof parsed.action_type === 'string' ? parsed.action_type : typeof parsed.event_type === 'string' ? parsed.event_type : copy.value.unknown }) || [])
function clear() { epoch++; props.methods.clear?.(); selected.value = null; platform.value = ''; page.value = null; pending.value = false; error.value = ''; stale.value = false }
async function announce() { await nextTick(); feedback.value?.focus() }
async function select(value) {
  clear()
  if (!value || !props.connected) return
  const life = epoch
  try {
    const admitted = await nativeObservationSelection(value, props.displayGraphId)
    if (life !== epoch || !props.connected) return
    selected.value = admitted; platform.value = admitted.launch.request.platforms[0]
  } catch (e) { if (life === epoch) { error.value = e?.code || 'invalid_reply'; await announce() } }
}
watch(() => props.selection, select, { deep: true, immediate: true, flush: 'sync' })
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.connected, value => { if (!value) clear() }, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
onBeforeUnmount(clear)
function changePlatform(event) {
  if (locked.value || !selected.value.launch.request.platforms.includes(event.target.value)) return
  epoch++; props.methods.clear?.(); platform.value = event.target.value; page.value = null; error.value = ''; stale.value = false
}
async function load(offset = 0) {
  if (locked.value) return
  const life = epoch, selection = JSON.parse(JSON.stringify(selected.value)), knownPage = page.value ? JSON.parse(JSON.stringify(page.value)) : null
  const payload = { schema_version: 1, launch_id: selection.launch.request.run_id, launch_sha256: selection.launch.launch_sha256, platform: platform.value, offset, limit: 20 }
  nativeObservationsPayload(payload)
  pending.value = true; error.value = ''; stale.value = false
  try {
    const value = await props.methods.page(JSON.parse(JSON.stringify(payload)), selection, knownPage)
    const admitted = await validateNativeObservationsResult(value, { graph: props.displayGraphId, payload, selection, knownPage })
    if (life !== epoch) return
    page.value = admitted
  } catch (e) {
    if (life !== epoch) return
    if (['unauthorized', 'origin_denied', 'disconnected', 'tombstoned'].includes(e?.code)) { clear(); error.value = e.code; await announce() }
    else stale.value = !!page.value
    error.value = e?.code || 'invalid_reply'
  } finally { if (life === epoch) { pending.value = false; await announce() } }
}
</script>
<template>
  <section class="native-observations" :lang="locale" aria-labelledby="native-observations-title" :aria-busy="pending">
    <h2 id="native-observations-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p>
    <p v-if="!selected">{{ copy.missing }}</p>
    <template v-if="selected">
      <dl class="provenance"><dt>{{ copy.run }}</dt><dd class="mono">{{ selected.launch.request.run_id }}</dd><dt>{{ copy.source }}</dt><dd>{{ selected.preparation.source.source_name }}<br><span class="mono">{{ selected.preparation.source.source_revision }}<br>{{ selected.preparation.source.source_sha256 }}</span></dd><dt>{{ copy.evidence }}</dt><dd class="mono">{{ selected.launch.receipt.evidence_sha256 }}</dd></dl>
      <label for="observations-platform">{{ copy.platform }}<select id="observations-platform" :value="platform" :disabled="locked" @change="changePlatform"><option v-for="name in selected.launch.request.platforms" :key="name" :value="name">{{ name }}</option></select></label>
      <div class="actions"><button class="load" type="button" :disabled="locked" @click="load(0)">{{ copy.load }}</button><button class="refresh" type="button" :disabled="locked || !page" @click="load(0)">{{ copy.refresh }}</button></div>
    </template>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ nativeObservationsError(copy, error) }}</span><span v-else-if="page">{{ copy.ready }}</span><span v-else-if="selected">{{ copy.unloaded }}</span><span v-if="stale"> {{ copy.stale }}</span></p>
    <template v-if="page">
      <dl class="counts"><dt>{{ copy.total }}</dt><dd>{{ page.total_records }}</dd><template v-for="(count, key) in page.counts" :key="key"><dt>{{ copy[key] }}</dt><dd>{{ count }}</dd></template></dl>
      <p v-if="!page.total_records" class="empty">{{ copy.empty }}</p>
      <p v-else-if="page.records.length" class="range">{{ copy.range }}: {{ page.offset }}–{{ page.offset + page.records.length - 1 }}</p>
      <p v-if="page.records.length < page.total_records">{{ copy.partial }}</p>
      <ol class="records" :start="page.offset + 1"><li v-for="(record, i) in page.records" :key="record.index" :data-index="record.index"><p class="event-label">{{ labels[i] }}</p><details><summary>{{ copy.literal }}</summary><pre>{{ record.raw_json }}</pre><p>{{ copy.recordHash }}<br><span class="mono">{{ record.record_sha256 }}</span></p></details></li></ol>
      <nav class="actions" :aria-label="copy.title"><button class="previous" type="button" :disabled="locked || page.offset === 0" @click="load(Math.max(0, page.offset - page.limit))">{{ copy.previous }}</button><button class="next" type="button" :disabled="locked || page.next_offset === null" @click="load(page.next_offset)">{{ copy.next }}</button></nav>
      <details class="manifest"><summary>{{ copy.files }}</summary><ul><li v-for="file in page.manifest.files" :key="file.name">{{ file.name }} · {{ copy.size }}: {{ file.size }}<br><span class="mono">{{ file.sha256 }}</span></li></ul></details>
    </template>
    <p class="limitations">{{ copy.distinction }}</p><p class="limitations">{{ copy.bounds }}</p>
    <div class="actions"><button class="clear" type="button" @click="clear">{{ copy.clear }}</button></div>
  </section>
</template>
<style scoped>
.native-observations{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere}.native-observations h2{font-size:24px;margin:0 0 12px}.native-observations p{line-height:1.6}.actions{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}.native-observations button,.native-observations select{font:inherit;min-height:44px;max-width:100%;padding:10px 14px;border:1px solid var(--border,#b9c4d2);border-radius:4px;background:var(--surface,#fff);color:inherit;white-space:normal;overflow-wrap:anywhere}.native-observations button{cursor:pointer}.native-observations button:disabled,.native-observations select:disabled{opacity:.6;cursor:not-allowed}.native-observations :focus-visible,.feedback:focus{outline:3px solid var(--accent,#2563eb);outline-offset:3px;scroll-margin:24px}.native-observations label{display:flex;flex-wrap:wrap;align-items:center;gap:12px}.provenance,.counts{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,3fr);gap:8px 16px}.native-observations dd{margin:0;min-width:0}.mono{font-family:monospace;overflow-wrap:anywhere}.feedback{min-height:24px}.limitations{color:var(--muted,#46566b);font-size:14px}.records{padding-left:28px}.records li{padding:12px 0;border-top:1px solid var(--border,#b9c4d2);min-width:0}.event-label{font-weight:600}.native-observations summary{cursor:pointer;min-height:44px;display:list-item;align-content:center;line-height:1.5}.native-observations pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;max-width:100%;min-width:0}.manifest ul{padding-left:20px}.manifest li{margin:12px 0}@media(max-width:600px){.native-observations{padding:16px}.provenance,.counts{grid-template-columns:minmax(0,1fr)}.native-observations dd{margin-bottom:8px}.actions button{flex:1 1 140px}}
</style>
