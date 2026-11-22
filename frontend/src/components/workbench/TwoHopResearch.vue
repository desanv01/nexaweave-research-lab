<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { twoHopCopy } from '../../i18n/twoHop.js'
import { twoHopQuestion } from '../../api/twoHop.js'

const props = defineProps({
  graphData: { type: Object, required: true },
  readPath: { type: Function, required: true },
  resetVersion: { type: Number, required: true },
  disabled: Boolean,
  locale: { type: String, required: true },
})
const copy = computed(() => twoHopCopy(props.locale))
const nodes = computed(() => new Map(props.graphData.nodes.map(node => [node.uuid, node.name])))
const source = ref(''), target = ref(''), question = ref('')
const acceptedQuestion = computed(() => twoHopQuestion(question.value))
const result = ref(null), failure = ref(''), pending = ref(false)
const locked = computed(() => props.disabled || pending.value)
const ready = computed(() => !locked.value && acceptedQuestion.value !== null && source.value !== target.value
  && nodes.value.has(source.value) && nodes.value.has(target.value))
let active = null

function nodeName(id) { return nodes.value.get(id) || id }

function clearResult() {
  active?.abort()
  active = null
  pending.value = false
  result.value = null
  failure.value = ''
}

function reset() {
  clearResult()
  source.value = ''
  target.value = ''
  question.value = ''
}

function choose(endpoint, id) {
  if (locked.value || !nodes.value.has(id)) return
  const pick = endpoint === 'source' ? source : target
  const other = endpoint === 'source' ? target : source
  if (other.value === id) return
  clearResult()
  pick.value = pick.value === id ? '' : id
}

watch([() => props.graphData, () => props.resetVersion], reset, { flush: 'sync' })
watch(question, clearResult, { flush: 'sync' })
onBeforeUnmount(clearResult)

async function submit() {
  if (!ready.value) return
  const graph = props.graphData
  const from = source.value, to = target.value, input = question.value
  const query = acceptedQuestion.value
  clearResult()
  const controller = new AbortController()
  active = controller
  pending.value = true
  const owns = () => active === controller && !controller.signal.aborted
    && props.graphData === graph && source.value === from && target.value === to && question.value === input

  try {
    const value = await props.readPath({
      schema_version: 2,
      operation: 'two_hop',
      display_graph_id: graph.graph_id,
      source_node_id: from,
      target_node_id: to,
      ...(query ? { question: query } : {}),
    }, controller.signal)
    if (owns()) result.value = value
  } catch (error) {
    if (owns()) failure.value = error?.code === 'invalid_reply' ? 'invalid' : 'failed'
  } finally {
    if (active === controller) {
      active = null
      pending.value = false
    }
  }
}
</script>

<template>
  <div>
    <slot name="nodes" :choose="choose" :source="source" :target="target" :copy="copy" :locked="locked" />
    <section class="two-hop-research" aria-labelledby="two-hop-title">
      <h3 id="two-hop-title">{{ copy.title }}</h3>
      <dl class="endpoints">
        <div><dt>{{ copy.start }}</dt><dd>{{ source ? nodeName(source) : copy.unselected }}</dd></div>
        <div><dt>{{ copy.destination }}</dt><dd>{{ target ? nodeName(target) : copy.unselected }}</dd></div>
      </dl>
      <form @submit.prevent="submit">
        <label for="two-hop-question">{{ copy.question }}</label>
        <textarea id="two-hop-question" v-model="question" rows="2" @compositionstart="clearResult" maxlength="4000"
          :disabled="disabled && !pending" :aria-invalid="acceptedQuestion === null || undefined"
          :aria-describedby="acceptedQuestion === null ? 'two-hop-question-help two-hop-question-error' : 'two-hop-question-help'"></textarea>
        <p id="two-hop-question-help" class="question-help">{{ copy.questionHelp }}</p>
        <p v-if="acceptedQuestion === null" id="two-hop-question-error" class="question-error" role="status">{{ copy.questionInvalid }}</p>
        <div class="actions">
          <button class="primary" type="submit" :disabled="!ready">{{ copy.find }}</button>
          <button v-if="source || target || question" type="button" :disabled="locked" @click="reset">{{ copy.clear }}</button>
        </div>
      </form>
      <p class="feedback" role="status" aria-live="polite" aria-atomic="true">
        <template v-if="pending">{{ copy.loading }}</template>
        <template v-else-if="failure">{{ copy[failure] }}</template>
        <template v-else-if="result">
          {{ copy[result.path_status] }}
          <span v-if="result.path && acceptedQuestion" class="path-rank">
            {{ result.rank_basis === 'directed_path_lexical_token_overlap' ? copy.rankMatched : copy.rankNoMatch }}
          </span>
          <span v-if="!result.path">{{ copy.scanned }}: {{ result.scope_coverage.scanned }}.</span>
          <span v-if="result.scope_coverage.truncated">{{ copy.truncated }}</span>
        </template>
      </p>
      <ol v-if="result?.path" class="hops">
        <li v-for="hop in result.path.hops" :key="hop.provider_id">
          <h4>{{ nodeName(hop.source_node_id) }} → {{ nodeName(hop.target_node_id) }}</h4>
          <p v-if="hop.name" class="relation">{{ hop.name }}</p>
          <p class="retained-text">{{ hop.fact }}</p>
          <details>
            <summary>{{ copy.clip }} ({{ hop.citations.length }})</summary>
            <ul class="citations">
              <li v-for="(citation, index) in hop.citations" :key="index">
                <strong>{{ citation.source_name || citation.source_revision }}</strong>
                <blockquote class="retained-text">{{ citation.excerpt }}</blockquote>
                <dl class="citation-source">
                  <dt>{{ copy.evidence }}</dt><dd>{{ citation.evidence_id }}</dd>
                  <dt>{{ copy.revision }}</dt><dd>{{ citation.source_revision }}</dd>
                  <template v-if="citation.declared_page !== null">
                    <dt>{{ copy.page }}</dt><dd>{{ citation.declared_page }}</dd>
                  </template>
                  <dt>{{ copy.offsets }}</dt><dd>{{ citation.start }}–{{ citation.end }}</dd>
                </dl>
              </li>
            </ul>
          </details>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.two-hop-research { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 16px; overflow-wrap: anywhere; }
h3, h4 { margin: 0 0 8px; }
.endpoints, .actions { display: flex; flex-wrap: wrap; gap: 8px 24px; }
.endpoints > div { min-width: 0; flex: 1 1 200px; }
dt { font-weight: 600; }
dd { margin: 0; }
button { min-height: 44px; max-width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--primary); font: inherit; cursor: pointer; }
button.primary { background: var(--primary); color: white; }
button:disabled { opacity: .65; cursor: default; }
button:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 3px solid var(--primary); outline-offset: 3px; }
form { margin-block-start: 12px; }
form label { display: block; font-weight: 600; margin-block-end: 4px; }
textarea { box-sizing: border-box; width: 100%; min-height: 44px; padding: 8px 12px; resize: vertical; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: inherit; font: inherit; }
.question-help, .question-error { margin: 4px 0 8px; }
.question-help { color: var(--muted); }
.question-error { font-weight: 600; }
.feedback:empty { margin: 0; }
.hops, .citations { padding-inline-start: 24px; }
.hops > li, .citations > li { margin-block-start: 12px; }
summary { cursor: pointer; min-height: 44px; padding-block: 10px; }
.relation { color: var(--muted); }
.retained-text { white-space: pre-wrap; }
blockquote { margin: 8px 0; padding-inline-start: 12px; border-inline-start: 3px solid var(--border); }
.citation-source { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px 12px; }
@media (max-width: 480px) { .citation-source { grid-template-columns: minmax(0, 1fr); } }
</style>
