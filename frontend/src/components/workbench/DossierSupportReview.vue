<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { sameAwareTimestamp } from '../../api/workbench.js'

const props = defineProps({
  dossier: { type: Object, required: true },
  claim: { type: Object, required: true },
  reference: { type: Object, required: true },
  section: { type: Object, required: true },
  displayGraphId: { type: String, required: true },
  loadSource: { type: Function, required: true },
  locale: { type: String, default: 'en' },
})
const labels = {
  en: {
    title: 'Review claim support', claim: 'Claim', verify: 'Verify retained passage', checking: 'Verifying retained passage…',
    unavailable: 'The retained passage could not be verified. Support remains not reviewed.',
    initial: 'Not reviewed', verified: 'Exact retained passage verified. Choose a judgment.',
    save: 'Record session judgment', saved: 'Session judgment',
    entailed: 'Entailed', contradicted: 'Contradicted', insufficient: 'Insufficient evidence',
    limits: 'This judgment applies to this passage in this view. It is not included in the dossier or its exports.',
  },
  zh: {
    title: '审阅主张的证据支持', claim: '主张', verify: '核实保留段落', checking: '正在核实保留段落…',
    unavailable: '无法核实保留段落。证据支持仍未审阅。',
    initial: '未审阅', verified: '已核实保留段落。请选择判断。',
    save: '记录本次会话的判断', saved: '本次会话判断',
    entailed: '支持', contradicted: '矛盾', insufficient: '证据不足',
    limits: '此判断仅适用于当前视图中的这个段落，不会写入证据卷宗或其导出文件。',
  },
  ms: {
    title: 'Semak sokongan dakwaan', claim: 'Dakwaan', verify: 'Sahkan petikan tersimpan', checking: 'Sedang mengesahkan petikan tersimpan…',
    unavailable: 'Petikan tersimpan tidak dapat disahkan. Sokongan masih belum disemak.',
    initial: 'Belum disemak', verified: 'Petikan tersimpan yang tepat telah disahkan. Pilih penilaian.',
    save: 'Rekod penilaian sesi', saved: 'Penilaian sesi',
    entailed: 'Disokong', contradicted: 'Bercanggah', insufficient: 'Bukti tidak mencukupi',
    limits: 'Penilaian ini terpakai pada petikan ini dalam paparan semasa. Penilaian tidak dimasukkan ke dalam dossier atau eksportnya.',
  },
}
const copy = computed(() => Object.hasOwn(labels, props.locale) ? labels[props.locale] : labels.en)
const choices = ['entailed', 'contradicted', 'insufficient']
const scopeKeys = ['schema_version', 'workspace_id', 'project_id', 'graph_id', 'run_id', 'branch_id', 'layer']
const binding = computed(() => {
  const { dossier, claim, reference, section, displayGraphId } = props
  const citation = reference.citation
  if (dossier.mode !== 'model_free_evidence_dossier' || claim.claim_class !== 'source' ||
      claim.scope.layer !== 'source' || claim.scope.run_id !== null || claim.scope.branch_id !== null ||
      reference.status !== 'resolved' || !citation || reference.claim_key !== claim.key ||
      !claim.reference_keys.includes(reference.key) || !claim.evidence_ids.includes(reference.evidence_id) ||
      reference.provider_id !== claim.provider_id || citation.evidence_id !== reference.evidence_id ||
      citation.project_id !== claim.scope.project_id || scopeKeys.some(key => reference.scope[key] !== claim.scope[key])) return null
  const sectionIndex = section.ordinal - 1
  const trace = dossier.research_trace[sectionIndex]
  if (!displayGraphId || dossier.sections[sectionIndex] !== section || !section.source_claim_keys.includes(claim.key) ||
      trace?.ordinal !== section.ordinal || trace.display_graph_ids.length !== 1 ||
      trace.display_graph_ids[0] !== displayGraphId || trace.scopes.length !== 1) return null
  const traced = trace.scopes[0]
  if (traced.display_graph_id !== displayGraphId || scopeKeys.some(key => traced.scope[key] !== claim.scope[key])) return null
  return {
    dossier,
    fingerprint: JSON.stringify([dossier.input_sha256, dossier.trace_sha256, dossier.records_sha256,
      section.ordinal, displayGraphId, claim.key, claim.text, reference.key, scopeKeys.map(key => claim.scope[key]), citation]),
    citation: JSON.parse(JSON.stringify(citation)),
  }
})
const verified = ref(null), choice = ref(''), judgment = ref(null), phase = ref('initial')
let generation = 0
function clear() {
  generation++
  verified.value = null
  choice.value = ''
  judgment.value = null
  phase.value = 'initial'
}
watch(() => [props.dossier, props.section, props.displayGraphId, props.claim, props.reference, binding.value?.fingerprint], clear, { flush: 'sync' })
onBeforeUnmount(clear)
function matches(value, citation) {
  const source = value.source
  const passage = value.passages.find(item => item.evidence_id === citation.evidence_id)
  return source.project_id === citation.project_id && source.source_revision === citation.source_revision &&
    source.source_name === citation.source_name && source.text_sha256 === citation.source_sha256 &&
    sameAwareTimestamp(source.recorded_at, citation.source_recorded_at) &&
    source.byte_length === citation.source_byte_length && source.codepoint_length === citation.source_codepoint_length &&
    value.offset_unit === citation.offset_unit && passage?.start === citation.start && passage.end === citation.end &&
    passage.page === citation.declared_page && passage.excerpt_sha256 === citation.excerpt_sha256 &&
    Array.from(value.text).slice(passage.start, passage.end).join('') === citation.excerpt
}
async function verify() {
  const current = binding.value
  if (phase.value === 'checking' || !current) return
  clear()
  const epoch = generation
  phase.value = 'checking'
  try {
    const citation = current.citation
    const source = await props.loadSource({ project_id: citation.project_id, source_revision: citation.source_revision }, current.dossier)
    if (epoch !== generation || binding.value !== current) return
    if (!matches(source, citation)) { phase.value = 'unavailable'; return }
    verified.value = current.fingerprint
    phase.value = 'verified'
  } catch {
    if (epoch === generation && binding.value === current) phase.value = 'unavailable'
  }
}
const canJudge = computed(() => !!binding.value && verified.value === binding.value.fingerprint)
function record() {
  if (!canJudge.value || !choices.includes(choice.value)) return
  judgment.value = { fingerprint: verified.value, value: choice.value }
}
</script>

<template>
  <section class="support-review" :aria-label="copy.title">
    <h3>{{ copy.title }}</h3>
    <p><strong>{{ copy.claim }}</strong></p>
    <p class="review-claim">{{ claim.text }}</p>
    <button type="button" class="support-verify" :disabled="!binding || phase === 'checking'" @click="verify">{{ copy.verify }}</button>
    <p class="support-status" role="status" aria-live="polite" aria-atomic="true">
      {{ canJudge && judgment?.fingerprint === verified ? copy.saved + ' · ' + copy[judgment.value] : copy[binding ? phase : 'unavailable'] }}
    </p>
    <form v-if="canJudge" @submit.prevent="record">
      <fieldset>
        <legend>{{ copy.title }}</legend>
        <label v-for="value in choices" :key="value">
          <input v-model="choice" type="radio" name="support-judgment" :value="value">{{ copy[value] }}
        </label>
      </fieldset>
      <button type="submit" class="support-record" :disabled="!choice">{{ copy.save }}</button>
    </form>
    <p class="support-note">{{ copy.limits }}</p>
  </section>
</template>

<style scoped>
.support-review{min-width:0;border-top:1px solid var(--border,#b9c4d2);padding-top:16px;margin-top:16px;overflow-wrap:anywhere}
h3{margin:0 0 8px;font-size:1rem}
p{margin:8px 0}
.review-claim{white-space:pre-wrap}
fieldset{border:0;padding:0;margin:0 0 12px;min-width:0}
legend{font-weight:650}
label{display:flex;align-items:center;gap:8px;min-height:44px;cursor:pointer}
input{margin:0;width:20px;height:20px}
button{min-height:44px;max-width:100%;padding:8px 12px;border:1px solid var(--border,#b9c4d2);border-radius:5px;background:var(--surface,#fff);color:var(--primary,#174d96);font:inherit;cursor:pointer;overflow-wrap:anywhere}
button:disabled{opacity:.55;cursor:default}
:where(input,button):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}
.support-note{font-size:.875rem;color:var(--muted,#526174)}
</style>
