<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { prepareSource } from '../../api/sourceLibrary.js'
import { copyFor } from '../../i18n/workbench.js'
const props = defineProps({ methods: { type: Object, required: true }, connected: Boolean, busy: Boolean, resetVersion: Number, locale: { type: String, default: 'en' } })
const emit = defineEmits(['inspected'])
const copy = computed(() => copyFor(props.locale).sources)
const windowData = ref(null), loadedAt = ref(''), inspected = ref(null), receipt = ref(null)
const name = ref(''), draft = ref(''), inputMode = ref('paste'), selectedFile = ref(null), fileInput = ref(null)
const prepared = ref(null), attempt = ref(null), preparing = ref(false), pending = ref(false), message = ref(''), error = ref('')
const inspector = ref(null), excerptPanel = ref(null), activePassage = ref(null)
let generation = 0, inspectorReturnFocus = null, passageReturnFocus = null
const disabled = computed(() => !props.connected || props.busy || preparing.value || pending.value)
const points = computed(() => inspected.value ? Array.from(inspected.value.text) : [])
const excerpt = computed(() => activePassage.value ? points.value.slice(activePassage.value.start, activePassage.value.end).join('') : '')
watch(inspected, value => emit('inspected', value), { flush: 'sync' })
const feedback = computed(() => {
  if (preparing.value) return copy.value.preparing
  if (pending.value) return copy.value.working
  if (attempt.value?.uncertain) return copy.value.unknown + (error.value ? ' ' + errorText() : '')
  return error.value ? errorText() : copy.value[message.value] || ''
})
function errorText() {
  if (error.value === 'source_denied') return copy.value.denied
  if (['source_unavailable', 'unsupported', 'not_found'].includes(error.value)) return error.value === 'not_found' && attempt.value ? copy.value.notFound : copy.value.unavailable
  if (['invalid_request', 'limit_exceeded'].includes(error.value)) return copy.value.invalid
  if (['invalid_reply', 'result_too_large'].includes(error.value)) return copy.value.invalidReply
  if (error.value === 'crypto_unavailable') return copy.value.crypto
  if (error.value === 'cancelled') return copy.value.cancelled
  return copy.value.failed
}
function clearDraft() { name.value = ''; draft.value = ''; selectedFile.value = null; prepared.value = null; if (fileInput.value) fileInput.value.value = '' }
function clearAll() {
  generation++; clearDraft(); windowData.value = null; loadedAt.value = ''; inspected.value = null; receipt.value = null
  inputMode.value = 'paste'
  attempt.value = null; activePassage.value = null; preparing.value = false; pending.value = false; error.value = ''; message.value = ''; inspectorReturnFocus = null; passageReturnFocus = null
}
watch(() => [props.connected, props.resetVersion], clearAll, { flush: 'sync' })
watch([name, draft, inputMode, selectedFile], () => { prepared.value = null; if (preparing.value) { generation++; preparing.value = false } })
onBeforeUnmount(clearAll)
function fileChanged(event) {
  const files = event.target.files
  selectedFile.value = files?.length === 1 ? files[0] : null
  if (files?.length !== 1) { event.target.value = ''; error.value = 'invalid_request' }
}
async function prepare() {
  if (disabled.value || attempt.value?.uncertain) return
  const epoch = ++generation
  preparing.value = true; prepared.value = null; message.value = ''; error.value = ''
  try {
    if (inputMode.value === 'file' && !selectedFile.value) { error.value = 'invalid_request'; return }
    const data = await prepareSource({ name: name.value, text: draft.value, file: inputMode.value === 'file' ? selectedFile.value : null })
    if (epoch !== generation || !props.connected) return
    prepared.value = data; message.value = 'ready'
  } catch (e) { if (epoch === generation) error.value = e.code || 'invalid_request' }
  finally { if (epoch === generation) preparing.value = false }
}
async function read(action, apply) {
  if (disabled.value) return
  const epoch = ++generation
  pending.value = true; message.value = ''; error.value = ''
  try {
    const data = await action()
    if (epoch !== generation || !props.connected) return
    await apply(data)
  } catch (e) { if (epoch === generation && props.connected) error.value = e.code || 'invalid_reply' }
  finally { if (epoch === generation) pending.value = false }
}
function load() {
  return read(() => props.methods.list(), data => {
    const project = data.sources[0]?.project_id
    if (project && (inspected.value && inspected.value.source.project_id !== project || receipt.value && receipt.value.source.project_id !== project)) throw { code: 'invalid_reply' }
    windowData.value = data; loadedAt.value = new Date().toISOString(); message.value = 'loaded'
  })
}
function inspect(source, event) {
  if (disabled.value) return
  inspectorReturnFocus = event?.currentTarget || null; inspected.value = null; activePassage.value = null
  const project = source.project_id || windowData.value?.sources[0]?.project_id || receipt.value?.source.project_id
  return read(() => props.methods.get({ source_revision: source.source_revision, ...(project ? { project_id: project } : {}) }), async data => {
    // Project is a consistency check only; server binding remains authoritative.
    if (project && data.source.project_id !== project) throw { code: 'invalid_reply' }
    if (source.text_sha256 && data.source.text_sha256 !== source.text_sha256) throw { code: 'invalid_reply' }
    if (source.format === 'text' && source.input_sha256 !== data.source.text_sha256 || source.source_name !== undefined && source.source_name !== data.source.source_name) throw { code: 'invalid_reply' }
    inspected.value = data; message.value = 'inspected'
    if (attempt.value?.source_revision === data.source.source_revision) attempt.value = { ...attempt.value, uncertain: false }
    await nextTick(); inspector.value?.focus()
  })
}
async function retain() {
  if (disabled.value || !prepared.value || attempt.value?.uncertain) return
  const epoch = ++generation, payload = prepared.value.payload
  // Never POST this prepared payload twice. Keep only reconciliation identity.
  attempt.value = { source_revision: payload.source_revision, input_sha256: payload.input_sha256, source_name: payload.source_name, format: payload.format, uncertain: true }
  clearDraft(); receipt.value = null; inspected.value = null; activePassage.value = null
  pending.value = true; message.value = ''; error.value = ''
  try {
    const data = await props.methods.retain(payload)
    if (epoch !== generation || !props.connected) return
    const project = windowData.value?.sources[0]?.project_id
    if (project && data.source.project_id !== project) throw { code: 'invalid_reply' }
    receipt.value = data; attempt.value = { ...attempt.value, uncertain: false, project_id: data.source.project_id, text_sha256: data.source.text_sha256 }; message.value = 'saved'
  } catch (e) {
    if (epoch !== generation || !props.connected) return
    error.value = e.code || 'invalid_reply'
    // Even an unreadable receipt can follow a completed mutation. Conservatively
    // reconcile every submitted attempt; do not make a rollback assertion.
  } finally { if (epoch === generation) pending.value = false }
}
function discard() { if (disabled.value) return; generation++; clearDraft(); message.value = ''; error.value = '' }
async function selectPassage(p, event) { passageReturnFocus = event.currentTarget; activePassage.value = p; await nextTick(); excerptPanel.value?.focus() }
async function closePassage() { activePassage.value = null; await nextTick(); passageReturnFocus?.focus() }
async function closeInspection() { inspected.value = null; activePassage.value = null; await nextTick(); inspectorReturnFocus?.focus() }
</script>
<template>
  <section class="source-library" aria-labelledby="sources-title">
    <header><h2 id="sources-title">{{ copy.title }}</h2><p class="help">{{ copy.intro }}</p></header>
    <p v-if="!connected" class="help">{{ copy.disconnected }}</p>
    <div class="source-actions"><button type="button" :disabled="disabled" @click="load">{{ copy.load }}</button></div>
    <p id="source-feedback" class="source-feedback" aria-live="polite" aria-atomic="true">{{ feedback }}</p>
    <div v-if="windowData" class="source-window">
      <h3>{{ copy.window }}</h3><p>{{ copy.current }} <time>{{ loadedAt }}</time></p><p class="help">{{ copy.stale }}</p>
      <p v-if="windowData.has_more" class="notice">{{ copy.more }}</p><p v-if="!windowData.sources.length">{{ copy.empty }}</p>
      <ul class="source-list"><li v-for="source in windowData.sources" :key="source.source_revision">
        <button type="button" :disabled="disabled" @click="inspect(source, $event)">{{ copy.select }}: {{ source.source_name }}</button>
        <dl><dt>{{ copy.revision }}</dt><dd class="mono">{{ source.source_revision }}</dd><dt>{{ copy.digest }}</dt><dd class="mono">{{ source.text_sha256 }}</dd><dt>{{ copy.bytes }}</dt><dd>{{ source.byte_length }}</dd><dt>{{ copy.codepoints }}</dt><dd>{{ source.codepoint_length }}</dd><dt>{{ copy.recorded }}</dt><dd><time>{{ source.recorded_at }}</time></dd></dl>
      </li></ul>
    </div>
    <form class="source-form" @submit.prevent="prepare">
      <fieldset aria-describedby="source-feedback source-file-help" :disabled="disabled || !!attempt?.uncertain"><legend>{{ copy.input }}</legend>
        <label for="source-name">{{ copy.name }}<input id="source-name" v-model="name" required autocomplete="off" spellcheck="false"></label>
        <div class="choices"><label><input v-model="inputMode" type="radio" value="paste">{{ copy.paste }}</label><label><input v-model="inputMode" type="radio" value="file">{{ copy.file }}</label></div>
        <label v-if="inputMode === 'paste'" for="source-text">{{ copy.text }}<textarea id="source-text" v-model="draft" rows="5" required spellcheck="false"></textarea></label>
        <label v-else for="source-file">{{ copy.choose }}<input id="source-file" ref="fileInput" type="file" accept=".txt,.md,.docx,.pdf" required aria-describedby="source-file-help" @change="fileChanged"></label>
        <p id="source-file-help" class="help">{{ copy.fileHint }}</p><button type="submit">{{ copy.prepare }}</button>
      </fieldset>
    </form>
    <div v-if="prepared" class="prepared"><p v-if="prepared.payload.format === 'pdf'" class="notice">{{ copy.pdfPrepare }}</p><h3>{{ copy.prepared }}</h3><dl><dt>{{ copy.name }}</dt><dd>{{ prepared.payload.source_name }}</dd><template v-if="prepared.filename"><dt>{{ copy.filename }}</dt><dd>{{ prepared.filename }}</dd></template><dt>{{ copy.format }}</dt><dd>{{ prepared.payload.format }}</dd><dt>{{ copy.bytes }}</dt><dd>{{ prepared.inputBytes }}</dd><dt>{{ copy.codepoints }}</dt><dd>{{ prepared.codepoints ?? copy.unknownLayout }}</dd><dt>{{ copy.inputDigest }}</dt><dd class="mono">{{ prepared.payload.input_sha256 }}</dd><dt>{{ copy.revision }}</dt><dd class="mono">{{ prepared.payload.source_revision }}</dd></dl><button class="primary" type="button" :disabled="disabled" @click="retain">{{ copy.retain }}</button></div>
    <div class="source-actions"><button type="button" :disabled="disabled" @click="discard">{{ copy.discard }}</button></div>
    <div v-if="attempt" class="attempt"><h3>{{ attempt.uncertain ? copy.uncertain : copy.revision }}</h3><p>{{ attempt.source_name }}</p><dl><dt>{{ copy.revision }}</dt><dd class="mono">{{ attempt.source_revision }}</dd><dt>{{ copy.inputDigest }}</dt><dd class="mono">{{ attempt.input_sha256 }}</dd></dl><button type="button" :disabled="disabled" @click="inspect(attempt, $event)">{{ copy.inspectAttempt }}</button></div>
    <article v-if="receipt" class="receipt"><h3>{{ copy.receipt }}</h3><p>{{ receipt.source.source_name }}</p><dl><dt>{{ copy.revision }}</dt><dd class="mono">{{ receipt.source.source_revision }}</dd><dt>{{ copy.digest }}</dt><dd class="mono">{{ receipt.source.text_sha256 }}</dd><dt>{{ copy.bytes }}</dt><dd>{{ receipt.source.byte_length }}</dd><dt>{{ copy.codepoints }}</dt><dd>{{ receipt.source.codepoint_length }}</dd><dt>{{ copy.recorded }}</dt><dd><time>{{ receipt.source.recorded_at }}</time></dd></dl><p class="notice">{{ copy.flags }}</p><p>{{ copy.noGraph }}</p><h4>{{ copy.extraction }}</h4><p>{{ receipt.extraction.format === 'pdf' ? copy.pdfExtraction : receipt.extraction.format === 'docx' ? copy.docxExtraction : copy.textExtraction }}</p><dl><dt>{{ copy.format }}</dt><dd>{{ receipt.extraction.format }}</dd><dt>{{ copy.inputDigest }}</dt><dd class="mono">{{ receipt.extraction.input_sha256 }}</dd></dl>
      <details v-if="receipt.extraction.blocks"><summary>{{ copy.blocks }} ({{ receipt.extraction.blocks.length }})</summary><ol><li v-for="block in receipt.extraction.blocks" :key="block.ordinal">{{ block.ordinal }} · {{ block.kind }} · {{ block.start }}–{{ block.end }}</li></ol></details>
      <div v-if="receipt.extraction.format === 'pdf'" class="pdf-summary"><dl><dt>{{ copy.pages }}</dt><dd>{{ receipt.extraction.page_count }}</dd><dt>{{ copy.emptyPages }}</dt><dd>{{ receipt.extraction.empty_page_count }}</dd><dt>{{ copy.pagePassages }}</dt><dd>{{ receipt.extraction.declared_passage_count }}</dd></dl><p>{{ copy.pdfEligibility }}</p><details><summary>{{ copy.pageDeclarations }}</summary><ol><li v-for="page in receipt.extraction.pages" :key="page.page">{{ copy.page }} {{ page.page }} · {{ page.start }}–{{ page.end }} · {{ page.empty ? copy.emptyPage : copy.textPage }}</li></ol></details></div><h4>{{ copy.passages }}</h4><ol><li v-for="p in receipt.passages" :key="p.evidence_id"><span v-if="p.page !== null">{{ copy.page }} {{ p.page }} · </span><span class="mono">{{ p.evidence_id }}</span> · {{ p.start }}–{{ p.end }}<p class="mono">{{ p.excerpt_sha256 }}</p></li></ol>
    </article>
    <article v-if="inspected" id="source-inspector" ref="inspector" class="source-inspector" tabindex="-1" aria-labelledby="source-inspector-title" @keydown.esc.stop.prevent="closeInspection">
      <h3 id="source-inspector-title">{{ inspected.source.source_name }}</h3><p class="notice">{{ copy.flags }}</p><p>{{ copy.noGraph }}</p><p class="mono">{{ inspected.source.source_revision }}</p>
      <details><summary>{{ copy.exact }}</summary><pre class="exact-text">{{ inspected.text }}</pre></details>
      <h4>{{ copy.passages }}</h4><p v-if="!inspected.passages.length">{{ copy.noPassages }}</p><ol><li v-for="p in inspected.passages" :key="p.evidence_id"><button type="button" :aria-expanded="activePassage?.evidence_id === p.evidence_id" aria-controls="source-excerpt" @click="selectPassage(p, $event)"><span v-if="p.page !== null">{{ copy.page }} {{ p.page }} · </span>{{ p.start }}–{{ p.end }} · {{ p.evidence_id }}</button></li></ol>
      <div v-if="activePassage" id="source-excerpt" ref="excerptPanel" class="source-excerpt" tabindex="-1" @keydown.esc.stop.prevent="closePassage"><dl><dt>{{ copy.evidence }}</dt><dd class="mono">{{ activePassage.evidence_id }}</dd><dt>{{ copy.offsets }}</dt><dd>{{ activePassage.start }}–{{ activePassage.end }}</dd><dt>{{ copy.page }}</dt><dd>{{ activePassage.page ?? copy.unknownLayout }}</dd><dt>{{ copy.excerptDigest }}</dt><dd class="mono">{{ activePassage.excerpt_sha256 }}</dd></dl><pre class="excerpt-text">{{ excerpt }}</pre><button type="button" @click="closePassage">{{ copy.close }}</button></div>
      <button type="button" @click="closeInspection">{{ copy.close }}</button>
    </article>
  </section>
</template>
<style scoped>
.source-library{background:var(--surface);color:var(--ink);padding:24px;border:1px solid var(--border);border-radius:8px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere}.source-library *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 12px}h3{font-size:1.1rem;margin:16px 0 8px}h4{font-size:1rem}p{margin:8px 0}.help{color:var(--muted);font-size:.875rem}.source-actions{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.source-feedback{min-height:24px}.source-form,.prepared,.attempt,.receipt,.source-inspector{margin-top:20px;padding-top:20px;border-top:1px solid var(--border)}fieldset{padding:0;margin:0;border:0;min-width:0}legend{color:var(--muted)}label{display:flex;flex-direction:column;gap:6px;font-weight:550;min-width:0}input,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--control-border);border-radius:5px;padding:10px 12px;min-height:44px;width:100%;min-width:0}textarea{resize:vertical}button{font:inherit;font-weight:550;min-height:44px;max-width:100%;padding:10px 16px;border:1px solid var(--control-border);border-radius:5px;color:var(--primary);background:var(--surface);cursor:pointer;overflow-wrap:anywhere;touch-action:manipulation}.primary{background:var(--primary);color:var(--surface)}button:disabled{opacity:.65;cursor:default}button:hover:not(:disabled),button:active:not(:disabled){filter:brightness(.92)}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible,.source-inspector:focus,.source-excerpt:focus{outline:3px solid var(--primary);outline-offset:3px}.choices{display:flex;flex-wrap:wrap;gap:16px;margin:16px 0}.choices label{flex-direction:row;align-items:center;min-height:44px}.choices input{width:20px;min-height:20px}.source-list{list-style:none;padding:0;display:grid;gap:16px}.source-list li{padding:16px;border:1px solid var(--border);border-radius:5px;min-width:0}dl{display:grid;grid-template-columns:minmax(0,1fr);gap:4px;margin:12px 0}dt{font-weight:600;color:var(--muted)}dd{margin:0 0 8px;min-width:0}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}.notice{border-left:3px solid var(--accent);padding:8px 12px}.exact-text,.excerpt-text{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;max-width:100%;font:inherit;line-height:1.6}.source-excerpt{padding:16px;background:var(--canvas);margin:16px 0}summary{cursor:pointer;min-height:44px;padding:10px 0}ol{padding-left:24px}li{margin-bottom:8px}@media(min-width:768px){dl{grid-template-columns:minmax(120px,1fr) minmax(0,3fr);gap:8px 16px}.source-list{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.source-library{padding:16px}}@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
