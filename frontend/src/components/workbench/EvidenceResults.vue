<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { copyFor } from '../../i18n/workbench.js'
import DossierExport from './DossierExport.vue'
const props = defineProps({ result: Object, locale: { type: String, default: 'en' } })
const copy = computed(() => copyFor(props.locale))
const selected = ref(null), inspector = ref(null), page = ref(0), passagePage = ref(0)
let opener = null
watch(() => props.result, () => { selected.value = null; opener = null; page.value = 0; passagePage.value = 0 })
const isDossier = computed(() => props.result?.mode === 'model_free_evidence_dossier')
const rows = computed(() => {
  if (!props.result) return []
  const r = props.result, claims = new Map((r.claims || []).map(c => [c.key, c]))
  const classes = [['source', 'source_claims', 'source_claim_keys'], ['simulation', 'simulation_observations', 'simulation_observation_keys'], ['other', 'other_claims', 'other_claim_keys']]
  return (isDossier.value ? r.sections : [{ ordinal: 1, heading: '' }]).flatMap(s => classes.flatMap(([label, facts, keys]) => (isDossier.value ? s[keys].map(k => claims.get(k)) : r[facts]).map(fact => ({ section: s, label, fact }))))
})
const visibleRows = computed(() => rows.value.slice(page.value * 10, (page.value + 1) * 10))
const traces = computed(() => !props.result ? [] : isDossier.value ? props.result.research_trace : [props.result])
const passages = computed(() => !props.result ? [] : isDossier.value ? props.result.summary.passage_coverage : props.result.passage_coverage)
const counts = computed(() => isDossier.value ? [props.result.summary.reference_links, props.result.summary.resolved_references, props.result.summary.unavailable_references] : [props.result.linked_citations, props.result.resolved_citations, props.result.unavailable_citations])
const refMap = computed(() => new Map((props.result?.references || []).map(r => [r.key, r])))
function references(f) {
  if (isDossier.value) return f.reference_keys.map(k => refMap.value.get(k))
  const citations = new Map(f.citations.map(c => [c.evidence_id, c]))
  return f.evidence_ids.map(id => ({ key: id, evidence_id: id, citation: citations.get(id) || null }))
}
async function select(reference, event) { opener = event.currentTarget; selected.value = reference; await nextTick(); inspector.value?.focus() }
function close() { selected.value = null; opener?.focus(); opener = null }
</script>
<template>
  <section class="evidence" :aria-label="copy.results">
    <div class="result-layout"><div class="result-main"><h2>{{ copy.results }}</h2>
      <template v-if="result">
        <DossierExport v-if="isDossier" :result="result" :locale="locale" />
        <ol v-if="isDossier"><li v-for="section in result.sections" :key="section.ordinal"><h3>{{ section.heading }}</h3><p>{{ copy.returned }}: {{ section.source_claim_keys.length + section.simulation_observation_keys.length + section.other_claim_keys.length }}</p></li></ol>
        <dl class="counts"><div v-for="(label, i) in [copy.linked, copy.resolved, copy.missing]" :key="label"><dt>{{ label }}</dt><dd>{{ counts[i] }}</dd></div></dl>
        <p v-if="!rows.length" class="empty">{{ copy.empty }}</p>
        <article v-for="row in visibleRows" :key="`${row.section.ordinal}-${row.fact.key || row.fact.provider_id}`" class="claim">
          <h3>{{ row.section.heading ? `${row.section.ordinal}. ${row.section.heading} · ` : '' }}{{ copy[row.label] }}</h3>
          <p class="claim-text">{{ row.fact.text || row.fact.fact }}</p><p class="subtle">{{ copy.support }}</p>
          <details><summary>{{ copy.identity }}</summary><p class="mono">{{ row.fact.key || row.fact.provider_id }}</p><p class="mono">{{ JSON.stringify(row.fact.scope) }}</p></details>
          <p v-if="!row.fact.evidence_ids.length" class="subtle">{{ copy.noLinks }}</p>
          <div v-else class="references"><button v-for="reference in references(row.fact)" :key="reference.key" type="button" @click="select(reference, $event)" :aria-expanded="selected?.key === reference.key" aria-controls="passage-inspector">{{ reference.citation ? copy.inspect : copy.unavailable }} · {{ reference.evidence_id }}</button></div>
        </article>
        <nav v-if="rows.length > 10" class="pagination" :aria-label="copy.results"><button type="button" :disabled="page === 0" @click="page--">{{ copy.previous }}</button><span>{{ copy.range }} {{ page + 1 }} / {{ Math.ceil(rows.length / 10) }}</span><button type="button" :disabled="(page + 1) * 10 >= rows.length" @click="page++">{{ copy.next }}</button></nav>
        <details v-for="(trace, i) in traces" :key="i" class="trace"><summary>{{ copy.trace }} {{ i + 1 }}</summary>
          <p v-if="trace.query" class="claim-text">{{ trace.query }}</p>
          <dl v-if="trace.request_sha256"><dt>{{ copy.requestDigest }}</dt><dd class="mono">{{ trace.request_sha256 }}</dd><dt>{{ copy.responseDigest }}</dt><dd class="mono">{{ trace.response_sha256 }}</dd><dt>{{ copy.validAt }}</dt><dd>{{ trace.valid_at ?? '—' }}</dd><dt>{{ copy.recordedBefore }}</dt><dd>{{ trace.recorded_before ?? '—' }}</dd></dl>
          <div v-for="scope in trace.scopes" :key="scope.display_graph_id"><p class="mono">{{ scope.display_graph_id }}</p><dl class="counts"><div v-for="key in ['scanned', 'eligible', 'excluded', 'unknown', 'returned', 'truncated']" :key="key"><dt>{{ copy[key] }}</dt><dd>{{ key === 'truncated' ? (scope[key] ? copy.yes : copy.no) : scope[key] }}</dd></div></dl></div>
          <details v-if="trace.competing_claim_candidates.length"><summary>{{ copy.candidates }} ({{ trace.competing_claim_candidates.length }})</summary><p>{{ copy.candidateNote }}</p><ol><li v-for="(candidate, ci) in trace.competing_claim_candidates" :key="ci"><p>{{ candidate.predicate }}</p><p class="mono">{{ candidate.subject_id }}</p><p class="mono">{{ candidate.provider_ids.join(', ') }}</p><p class="mono">{{ JSON.stringify(candidate.scope) }}</p></li></ol></details>
        </details>
        <details v-if="passages.length" class="trace"><summary>{{ copy.coverage }} ({{ passages.length }})</summary><dl v-for="passage in passages.slice(passagePage * 10, (passagePage + 1) * 10)" :key="`${passage.project_id}-${passage.source_revision}`"><dt>{{ copy.revision }}</dt><dd class="mono">{{ passage.source_revision }}</dd><dt>{{ copy.retained }}</dt><dd>{{ passage.retained_codepoints }}</dd><dt>{{ copy.retrieved }}</dt><dd>{{ passage.retrieved_codepoints }}</dd></dl><nav v-if="passages.length > 10" class="pagination" :aria-label="copy.coverage"><button type="button" :disabled="!passagePage" @click="passagePage--">{{ copy.previous }}</button><span>{{ copy.range }} {{ passagePage + 1 }} / {{ Math.ceil(passages.length / 10) }}</span><button type="button" :disabled="(passagePage + 1) * 10 >= passages.length" @click="passagePage++">{{ copy.next }}</button></nav></details>
      </template><p v-else class="empty">{{ copy.idle }}</p>
    </div>
    <aside id="passage-inspector" ref="inspector" tabindex="-1" class="inspector" :aria-label="copy.inspector" @keydown.esc="close"><h2>{{ copy.inspector }}</h2>
      <template v-if="selected"><button type="button" @click="close">{{ copy.close }}</button><p class="mono">{{ selected.key }}</p>
        <template v-if="selected.citation"><h3>{{ selected.citation.source_name }}</h3><p class="excerpt">{{ selected.citation.excerpt }}</p><dl><dt>{{ copy.evidenceId }}</dt><dd class="mono">{{ selected.citation.evidence_id }}</dd><dt>{{ copy.revision }}</dt><dd class="mono">{{ selected.citation.source_revision }}</dd><dt>{{ copy.offsets }}</dt><dd>{{ selected.citation.start }} – {{ selected.citation.end }} · {{ selected.citation.offset_unit }}</dd><template v-if="selected.citation.declared_page !== null"><dt>{{ copy.page }}</dt><dd>{{ selected.citation.declared_page }}</dd></template><dt>{{ copy.digest }}</dt><dd class="mono">{{ selected.citation.source_sha256 }}</dd></dl><p class="subtle">{{ copy.original }}</p></template>
        <p v-else>{{ copy.unavailable }}</p>
      </template><p v-else class="subtle">{{ copy.inspect }}</p>
    </aside></div>
    <div class="limitations"><h3>{{ copy.limitations }}</h3><p>{{ copy.limitationText }}</p></div>
  </section>
</template>
<style scoped>
.evidence *{box-sizing:border-box}
.evidence,.result-main,.inspector{min-width:0}.result-layout{display:grid;gap:24px}h2{font-size:1.35rem;margin:0 0 16px}h3{font-size:1rem;margin:0 0 8px}p,dd,li{overflow-wrap:anywhere}p{margin:8px 0}.claim{background:var(--surface);border:1px solid var(--border);padding:20px;margin-bottom:12px;border-radius:8px}.claim-text,.excerpt{white-space:pre-wrap;line-height:1.65}.excerpt{background:var(--canvas);padding:16px;border-left:3px solid var(--primary)}.subtle{color:var(--muted)}.mono{font-family:ui-monospace,Consolas,monospace;font-size:.875rem;overflow-wrap:anywhere}.counts{display:flex;flex-wrap:wrap;gap:12px 24px;margin:0 0 20px}dt{color:var(--muted);font-size:.875rem}dd{margin:0 0 12px}.counts dd{font-size:1.15rem;margin:0;font-variant-numeric:tabular-nums}.references{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}button{background:var(--surface);border:1px solid var(--border);border-radius:5px;color:var(--primary);padding:10px 12px;min-height:44px;font:inherit;cursor:pointer;overflow-wrap:anywhere;max-width:100%}button:disabled{opacity:.55;cursor:default}button:hover:not(:disabled){background:var(--canvas)}button:focus-visible,summary:focus-visible,.inspector:focus-visible{outline:3px solid var(--primary);outline-offset:3px}details{margin:12px 0}summary{min-height:44px;padding:10px 0;box-sizing:border-box;cursor:pointer;overflow-wrap:anywhere}.trace{border-top:1px solid var(--border);padding:8px 0}.inspector{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;align-self:start}.pagination{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:16px 0}.limitations{border-left:3px solid var(--accent);padding:16px;margin-top:24px;background:var(--surface)}.empty{padding:24px 0;color:var(--muted)}@media(min-width:1024px){.result-layout{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)}}
</style>
