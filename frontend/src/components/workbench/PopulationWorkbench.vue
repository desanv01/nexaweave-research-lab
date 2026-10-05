<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { populationOptions, populationTypeLabels, validatePopulationPreview, validatePopulationExport } from '../../api/populationWorkbench.js'
import { populationCopyFor, populationErrorKey } from '../../i18n/populationWorkbench.js'
const props = defineProps({ methods: { type: Object, required: true }, connected: Boolean, busy: Boolean, resetVersion: Number, locale: { type: String, default: 'en' }, typeLabels: { type: Array, default: () => [] } })
const copy = computed(() => populationCopyFor(props.locale))
const types = computed(() => populationTypeLabels(props.typeLabels))
const selectedTypes = ref([]), maximum = ref('10'), seed = ref('0'), preview = ref(null), submitted = ref(null), pending = ref(false), feedback = ref(''), page = ref(0), selected = ref(null)
const inspector = ref(null), feedbackNode = ref(null)
const preparedUrl = ref(null), preparedPlatform = ref('')
let generation = 0, returnFocus = null, ownedUrl = null, timer = null
const disabled = computed(() => !props.connected || props.busy || pending.value)
const message = computed(() => pending.value ? copy.value.working : feedback.value ? copy.value[feedback.value] : props.connected ? copy.value.empty : copy.value.disconnected)
const whole = (value, min, max) => typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) >= min && Number(value) <= max
const maximumInvalid = computed(() => !whole(maximum.value, 1, 100))
const seedInvalid = computed(() => !whole(seed.value, 0, 4294967295))
const ground = computed(() => selected.value && preview.value?.grounding[selected.value.source_entity_uuid])
function cleanup() {
  if (timer !== null) { clearTimeout(timer); timer = null }
  preparedUrl.value = null; preparedPlatform.value = ''
  if (ownedUrl !== null) { const url = ownedUrl; ownedUrl = null; try { URL.revokeObjectURL(url) } catch { /* owned browser resource */ } }
}
function invalidate(changed = false) { generation++; cleanup(); preview.value = null; submitted.value = null; selected.value = null; returnFocus = null; page.value = 0; pending.value = false; feedback.value = changed ? 'changed' : '' }
watch(() => [props.connected, props.resetVersion], () => invalidate(), { flush: 'sync' })
watch([maximum, seed, selectedTypes], () => invalidate(props.connected), { deep: true, flush: 'sync' })
watch(types, () => { selectedTypes.value = selectedTypes.value.filter(type => types.value.includes(type)); invalidate(props.connected) }, { flush: 'sync' })
watch(feedback, value => props.methods.feedback?.(value), { flush: 'sync' })
onBeforeUnmount(() => invalidate())
function options() {
  if (maximumInvalid.value || seedInvalid.value || selectedTypes.value.some(type => !types.value.includes(type))) { const e = new Error('invalid_request'); e.code = 'invalid_request'; throw e }
  return populationOptions({ max_agents: Number(maximum.value), seed: Number(seed.value), types: selectedTypes.value })
}
async function showPreview() {
  if (disabled.value) return
  invalidate()
  let snapshot
  try { snapshot = options() } catch { feedback.value = 'invalid'; await nextTick(); feedbackNode.value?.focus(); return }
  const epoch = ++generation; pending.value = true
  try {
    const result = await props.methods.preview(snapshot)
    if (epoch !== generation || !props.connected) return
    const admitted = validatePopulationPreview(result, result?.graph_id, snapshot)
    preview.value = admitted; submitted.value = populationOptions(snapshot); feedback.value = 'ready'
  } catch (error) {
    if (epoch === generation && props.connected) { const code = populationErrorKey(error.code); if (code === 'denied') invalidate(); feedback.value = code; await nextTick(); feedbackNode.value?.focus() }
  } finally { if (epoch === generation) pending.value = false }
}
async function inspect(profile, event) { selected.value = profile; returnFocus = event.currentTarget; await nextTick(); inspector.value?.focus() }
async function close() { selected.value = null; await nextTick(); if (returnFocus?.isConnected) returnFocus.focus(); else feedbackNode.value?.focus(); returnFocus = null }
const organizationTypes = ['mediaoutlet', 'socialmediaplatform', 'university', 'governmentagency', 'ngo', 'organization']
const individualTypes = ['student', 'alumni', 'publicfigure', 'expert', 'faculty']
function category(type) { const value = type.toLowerCase(); return copy.value[organizationTypes.includes(value) ? 'organization' : individualTypes.includes(value) ? 'individual' : 'other'] }
async function prepareExport(platform) {
  if (disabled.value || !preview.value || !submitted.value) return
  cleanup(); feedback.value = ''; const epoch = ++generation; pending.value = true
  const admitted = preview.value, snapshot = populationOptions(submitted.value)
  try {
    const output = await props.methods.export(platform, snapshot, admitted)
    if (epoch !== generation || !props.connected || preview.value !== admitted) return
    try {
      const expectedMime = platform === 'twitter' ? /^text\/csv\s*;\s*charset=utf-8$/i : /^application\/json\s*;\s*charset=utf-8$/i
      if (!(output.bytes instanceof Uint8Array) || output.bytes.byteLength > 2097152 || !expectedMime.test(output.mime)) throw new Error('invalid_reply')
      const raw = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(output.bytes)
      validatePopulationExport(raw, platform, admitted)
    } catch {
      const error = new Error('invalid_reply'); error.code = 'invalid_reply'; throw error
    }
    if (epoch !== generation || !props.connected) return
    ownedUrl = URL.createObjectURL(new Blob([output.bytes], { type: output.mime }))
    preparedUrl.value = ownedUrl; preparedPlatform.value = platform; feedback.value = 'prepared'
  } catch (error) { if (epoch === generation && props.connected) { cleanup(); const code = populationErrorKey(error.code || 'invalid_reply'); if (code === 'denied') invalidate(); feedback.value = code } }
  finally { if (epoch === generation) pending.value = false }
}
function savePrepared(event) {
  if (disabled.value || !preview.value || !submitted.value || !ownedUrl || preparedUrl.value !== ownedUrl || event.currentTarget.getAttribute('href') !== ownedUrl) { event.preventDefault(); return }
  // Keep the actual native link alive through its default click action. Only
  // this immediate user action starts a download; preparation never clicks it.
  if (timer !== null) clearTimeout(timer)
  feedback.value = 'requested'; timer = setTimeout(cleanup, 1000)
}
</script>
<template>
  <section class="population-workbench" aria-labelledby="population-title">
    <h2 id="population-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p><p class="limitation">{{ copy.limits }}</p>
    <form @submit.prevent="showPreview" novalidate>
      <fieldset :disabled="disabled"><legend>{{ copy.selection }}</legend>
        <div class="options"><label for="population-maximum">{{ copy.maximum }}<input id="population-maximum" v-model="maximum" type="text" inputmode="numeric" :aria-invalid="maximumInvalid" :aria-describedby="maximumInvalid ? 'population-maximum-error' : undefined"><span v-if="maximumInvalid" id="population-maximum-error" class="error">{{ copy.integerError }}</span></label><label for="population-seed">{{ copy.seed }}<input id="population-seed" v-model="seed" type="text" inputmode="numeric" :aria-invalid="seedInvalid" :aria-describedby="seedInvalid ? 'population-seed-error' : undefined"><span v-if="seedInvalid" id="population-seed-error" class="error">{{ copy.integerError }}</span></label></div>
        <details><summary>{{ copy.types }}</summary><p id="population-types-help">{{ copy.allTypes }}</p><div class="type-choices"><label v-for="(type, index) in types" :key="type" :for="`population-type-${index}`"><input :id="`population-type-${index}`" v-model="selectedTypes" type="checkbox" :value="type" aria-describedby="population-types-help">{{ type }}</label></div></details>
        <button class="primary preview" type="submit">{{ copy.preview }}</button>
      </fieldset>
    </form>
    <p ref="feedbackNode" class="feedback" tabindex="-1">{{ message }}</p>
    <template v-if="preview">
      <p>{{ copy.counts }}: {{ preview.selected_count }} / {{ preview.eligible_count }} · {{ copy.date }}: {{ preview.profile_date }}</p>
      <ul class="profiles"><li v-for="profile in preview.profiles.slice(page * 10, (page + 1) * 10)" :key="profile.source_entity_uuid"><p><strong>{{ profile.name }}</strong> · {{ profile.source_entity_type }} · {{ category(profile.source_entity_type) }}</p><button type="button" :aria-expanded="selected?.source_entity_uuid === profile.source_entity_uuid" aria-controls="population-inspector" @click="inspect(profile, $event)">{{ copy.inspect }} — {{ profile.user_name }}</button></li></ul>
      <nav v-if="preview.profiles.length > 10" class="actions" :aria-label="copy.title"><button type="button" :disabled="page === 0" @click="page--">{{ copy.previous }}</button><span>{{ copy.page }} {{ page + 1 }} / {{ Math.ceil(preview.profiles.length / 10) }}</span><button type="button" :disabled="(page + 1) * 10 >= preview.profiles.length" @click="page++">{{ copy.next }}</button></nav>
      <div class="actions"><button class="twitter-download" type="button" :disabled="disabled" @click="prepareExport('twitter')">{{ copy.prepareTwitter }}</button><button class="reddit-download" type="button" :disabled="disabled" @click="prepareExport('reddit')">{{ copy.prepareReddit }}</button><a v-if="preparedUrl" class="save-link" :href="preparedUrl" :download="preparedPlatform === 'twitter' ? 'oasis-twitter-profiles.csv' : 'oasis-reddit-profiles.json'" :aria-disabled="disabled" :tabindex="disabled ? -1 : 0" @click="savePrepared">{{ preparedPlatform === 'twitter' ? copy.saveTwitter : copy.saveReddit }}</a></div><p class="help">{{ copy.exportLimit }}</p>
    </template>
    <aside v-if="selected && ground" id="population-inspector" ref="inspector" tabindex="-1" aria-labelledby="population-inspector-title" @keydown.esc.stop.prevent="close">
      <h3 id="population-inspector-title">{{ copy.inspect }} — {{ selected.name }}</h3><button type="button" @click="close">{{ copy.close }}</button>
      <h4>{{ copy.assumptions }}</h4><dl><dt>{{ copy.username }}</dt><dd>{{ selected.user_name }}</dd><dt>{{ copy.bio }}</dt><dd class="complete-text">{{ selected.bio }}</dd><dt>{{ copy.persona }}</dt><dd class="complete-text">{{ selected.persona }}</dd><dt>{{ copy.type }}</dt><dd>{{ selected.source_entity_type }} · {{ category(selected.source_entity_type) }}</dd></dl>
      <details><summary>{{ copy.traits }}</summary><pre>{{ JSON.stringify({ user_id: selected.user_id, created_at: selected.created_at, ...Object.fromEntries(preview.synthetic_fields.map(key => [key, selected[key]])) }, null, 2) }}</pre></details>
      <h4>{{ copy.grounding }}</h4><dl><dt>{{ copy.source }}</dt><dd>{{ ground.source_entity_uuid }}</dd><dt>{{ copy.labels }}</dt><dd>{{ ground.labels.join(', ') }}</dd><dt>{{ copy.summary }}</dt><dd class="complete-text">{{ ground.summary ?? copy.none }}</dd><dt>{{ copy.attributes }}</dt><dd><pre>{{ JSON.stringify(ground.attributes, null, 2) }}</pre></dd></dl>
      <details><summary>{{ copy.references }}</summary><h5>{{ copy.episodes }}</h5><pre>{{ ground.episode_ids.length ? ground.episode_ids.join('\n') : copy.none }}</pre><h5>{{ copy.evidence }}</h5><pre>{{ ground.evidence_ids.length ? ground.evidence_ids.join('\n') : copy.none }}</pre></details>
      <h4>{{ copy.facts }}</h4><p v-if="!ground.facts.length">{{ copy.none }}</p><article v-for="fact in ground.facts" :key="fact.edge_uuid" class="fact"><p>{{ fact.direction === 'outgoing' ? copy.outgoing : copy.incoming }} · {{ fact.edge_name }}</p><dl><dt>{{ copy.edge }}</dt><dd>{{ fact.edge_uuid }}</dd><dt>{{ copy.endpoints }}</dt><dd>{{ fact.source_node_uuid }} → {{ fact.target_node_uuid }}</dd><dt>{{ copy.fact }}</dt><dd class="complete-text">{{ fact.fact ?? copy.none }}</dd></dl><details><summary>{{ copy.references }}</summary><h5>{{ copy.episodes }}</h5><pre>{{ fact.episode_ids.length ? fact.episode_ids.join('\n') : copy.none }}</pre><h5>{{ copy.evidence }}</h5><pre>{{ fact.evidence_ids.length ? fact.evidence_ids.join('\n') : copy.none }}</pre></details></article>
    </aside>
  </section>
</template>
<style scoped>
.population-workbench{min-width:0;margin:0 0 24px;padding:24px;border:1px solid var(--border);border-radius:8px;background:var(--surface);overflow-wrap:anywhere}h2{font-size:1.25rem;margin:0 0 12px}h3,h4,h5{margin:16px 0 8px}p{margin:8px 0}.limitation{border-left:3px solid var(--accent);padding:8px 12px;color:var(--muted)}fieldset{border:0;margin:16px 0;padding:0;min-width:0}legend,.help{color:var(--muted)}.options{display:grid;gap:16px;margin:12px 0}label{display:flex;flex-direction:column;gap:6px;min-width:0}input{box-sizing:border-box;min-width:0;width:100%;min-height:44px;border:1px solid var(--control-border);border-radius:5px;padding:10px 12px;background:var(--surface);color:var(--ink);font:inherit}.type-choices{display:flex;flex-wrap:wrap;gap:8px 16px}.type-choices label{min-height:44px;flex-direction:row;align-items:center}.type-choices input{width:20px;min-height:20px}.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}button{box-sizing:border-box;min-height:44px;max-width:100%;padding:10px 14px;border:1px solid var(--border);border-radius:5px;background:var(--surface);color:var(--primary);font:inherit;cursor:pointer;overflow-wrap:anywhere}.primary{background:var(--primary);color:white;border-color:var(--primary)}button:disabled{opacity:.65;cursor:default}button:focus-visible,input:focus-visible,summary:focus-visible,aside:focus-visible,.feedback:focus-visible{outline:3px solid var(--primary);outline-offset:3px}summary{min-height:44px;cursor:pointer;padding:10px 0}.feedback{padding:8px 0;color:var(--muted)}.error{color:var(--ink);font-size:.875rem}.profiles{list-style:none;padding:0;margin:16px 0}.profiles li,.fact{border-top:1px solid var(--border);padding:12px 0}aside{margin-top:24px;padding:16px;background:var(--canvas);border:1px solid var(--border);border-radius:5px;min-width:0}dl{margin:12px 0}dt{font-weight:650;margin-top:8px}dd{margin:4px 0 12px}.complete-text,pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}pre{font:.875rem/1.6 ui-monospace,Consolas,monospace;margin:8px 0;max-width:100%}@media(min-width:768px){.options{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.population-workbench{padding:16px}}
</style>
<style scoped>
.save-link{box-sizing:border-box;display:inline-flex;align-items:center;min-height:44px;max-width:100%;padding:10px 14px;border:1px solid var(--primary);border-radius:5px;background:var(--primary);color:var(--surface);font:inherit;text-decoration:underline;overflow-wrap:anywhere}.save-link:focus-visible{outline:3px solid var(--primary);outline-offset:3px}.save-link[aria-disabled="true"]{opacity:.65;cursor:default}
</style>
