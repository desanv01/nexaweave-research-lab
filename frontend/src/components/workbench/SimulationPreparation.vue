<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { configurationIdentity, preparationIdentity, preparationInteger, preparationOptions, preparationSource, newPreparationId, validatePreparationResult } from '../../api/simulationPreparation.js'
import { populationTypeLabels } from '../../api/populationWorkbench.js'
import { preparationCopyFor, preparationError } from '../../i18n/simulationPreparation.js'
const props = defineProps({ methods: { type: Object, required: true }, connected: Boolean, busy: Boolean, resetVersion: { type: Number, default: 0 }, locale: { type: String, default: 'en' }, source: { type: Object, default: null }, sourceCatalog: { type: Array, default: null }, displayGraphId: { type: String, default: '' }, typeLabels: { type: Array, default: () => [] } })
const emit = defineEmits(['ready', 'cleared'])
const copy = computed(() => preparationCopyFor(props.locale)), labels = computed(() => populationTypeLabels(props.typeLabels))
const selected = ref([]), maximum = ref('10'), seed = ref('0'), rounds = ref('10'), requirement = ref(''), twitter = ref(true), reddit = ref(false)
const selectedExtras = ref([])
const additionalSources = computed(() => {
  const anchor = props.source, seen = new Set(anchor ? [anchor.source_revision] : [])
  return (props.sourceCatalog || []).filter(source => {
    if (!anchor || !source || source.project_id !== anchor.project_id || seen.has(source.source_revision)) return false
    seen.add(source.source_revision); return true
  })
})
const plan = ref(null), durable = ref(null), pending = ref(false), attempted = ref(false), error = ref(''), notice = ref(''), feedback = ref(null)
const suggestion = ref('')
const profileActor = ref(''), profileResult = ref(null), profileError = ref(''), profilePending = ref(false)
const inspectionReady = computed(() => {
  const current = plan.value || durable.value
  return current?.state === 'ready' && current.receipt ? current : null
})
const profileReady = computed(() => props.methods.profiles ? inspectionReady.value : null)
const configurationReady = computed(() => {
  const ready = inspectionReady.value, source = props.source
  return props.methods.configuration && ready && source && ready.source.source_revision === source.source_revision &&
    ready.scope.project_id === source.project_id && ready.display_graph_id === props.displayGraphId ? ready : null
})
const configurationResult = ref(null), configurationError = ref(''), configurationPending = ref(false)
const timeFields = ['total_simulation_hours', 'minutes_per_round', 'agents_per_hour_min', 'agents_per_hour_max']
const activityPeriods = ['peak', 'off_peak', 'morning', 'work']
const platformFields = ['viral_threshold', 'recency_weight', 'popularity_weight', 'relevance_weight', 'echo_chamber_strength']
const platformName = platform => platform === 'twitter' ? 'Twitter' : 'Reddit'
let configurationOwner = null
function clearConfiguration() {
  configurationOwner?.abort()
  configurationOwner = null
  configurationResult.value = null
  configurationError.value = ''
  configurationPending.value = false
}
watch(configurationReady, clearConfiguration, { flush: 'sync' })
let profileOwner = null
function clearProfile(clearActor = true) {
  profileOwner?.abort()
  profileOwner = null
  profileResult.value = null
  profileError.value = ''
  profilePending.value = false
  if (clearActor) profileActor.value = ''
}
watch(profileReady, () => clearProfile(), { flush: 'sync' })
watch(profileActor, () => clearProfile(false), { flush: 'sync' })
let lifetime = 0, reviewEpoch = 0, updatingCatalog = false
const locked = computed(() => !props.connected || props.busy || pending.value)
const canStart = computed(() => !locked.value && plan.value?.state === 'planned' && plan.value.authorization.model_calls_enabled && !!plan.value.authorization.ceiling_microusd && !(attempted.value && durable.value?.operation_id === plan.value.operation_id))
const records = computed(() => [plan.value && { key: 'plan', value: plan.value }, durable.value && durable.value.operation_id !== plan.value?.operation_id && { key: 'durable', value: durable.value }].filter(Boolean))
function changed() { clearProfile(); clearConfiguration(); reviewEpoch++; if (plan.value) notice.value = 'changed'; plan.value = null; error.value = ''; suggestion.value = ''; emit('ready', null) }
watch([selected, maximum, seed, rounds, requirement, twitter, reddit], changed, { deep: true, flush: 'sync' })
watch(selectedExtras, () => { if (!updatingCatalog) changed() }, { deep: true, flush: 'sync' })
watch(() => props.source, () => { selectedExtras.value = []; changed() }, { deep: true, flush: 'sync' })
watch(() => props.sourceCatalog, () => {
  const hadExtras = selectedExtras.value.length > 0
  updatingCatalog = true
  try { selectedExtras.value = [] } finally { updatingCatalog = false }
  if (hadExtras) {
    reviewEpoch++; error.value = ''
    if (plan.value?.state === 'planned' && !(attempted.value && durable.value?.operation_id === plan.value.operation_id)) {
      plan.value = null; notice.value = 'changed'
    }
  }
  // Library metadata is a draft input. Existing READY/native/report handoffs
  // retain their frozen identity until an intentional source/option/reset edit.
}, { deep: true, flush: 'sync' })
// Canonical labels are detached, so in-place prop edits retain an old snapshot.
// Batch array edits before comparing their final semantic choices.
watch(labels, (current, previous) => {
  if (current.length !== previous.length || current.some((label, index) => label !== previous[index])) changed()
}, { flush: 'pre' })
function clear() {
  clearProfile()
  clearConfiguration()
  lifetime++; reviewEpoch++; plan.value = null; durable.value = null; attempted.value = false; pending.value = false
  selected.value = []; selectedExtras.value = []; maximum.value = '10'; seed.value = '0'; rounds.value = '10'; requirement.value = ''; twitter.value = true; reddit.value = false
  error.value = ''; notice.value = ''; suggestion.value = ''
  emit('ready', null); emit('cleared')
}
watch(() => props.resetVersion, clear, { flush: 'sync' })
watch(() => props.connected, connected => { if (!connected) clear() }, { flush: 'sync' })
watch(() => props.displayGraphId, clear, { flush: 'sync' })
onBeforeUnmount(clear)
async function announce() { await nextTick(); feedback.value?.focus() }
const uncertainGoal = /[?？"'‘’“”「」『』`]|\b(?:no|not|never|cannot|without|except|avoid|exclude|skip|neither|nor|or|either|instead|rather|maybe|perhaps|if|whether|unless|tidak|jangan|tanpa|bukan|kecuali|elak(?:kan)?|atau|mungkin|jika)\b|[不別别無无]|禁止|避免|排除|或者|或是|是否/u
const chosenPlatforms = /(?:(both|kedua-dua|同时|同時)\s*)?\b(twitter|reddit)\b(?:\s*(?:and|&|dan|和|与|與|及|、)\s*\b(twitter|reddit)\b)?(?:\s+(?:only|sahaja))?$/u
const nonInstructionContext = /\b(?:examples?|samples?|hypothetical(?:ly)?|quot(?:e|es|ed|ing|ation|ations)|ignor(?:e|es|ed|ing)|(?:disregard|discard|reject)(?:s|ed|ing)?|vetoe?d|contoh|petikan|abaikan|tolak|ditolak)\b|例子|示例|範例|范例|举例|舉例|引用|忽略|忽視|忽视|拒绝|拒絕/u
const platformDirectives = [
  /^(?:please\s+)?(?:use|compare|platforms?\s*[:：])$/u,
  /^(?:please\s+)?(?:run|simulate|prepare|test|model)(?:\s+(?:it|(?:(?:a|the|this)\s+)?(?:simulation|scenario|experiment)))?\s+(?:on|across|using)$/u,
  /^(?:sila\s+)?(?:guna|gunakan|bandingkan|platform\s*[:：])$/u,
  /^(?:sila\s+)?(?:jalankan|simulasikan|uji)(?:\s+(?:simulasi|senario)(?:\s+ini)?)?\s+di$/u,
  /^(?:使用|比较|比較|平台\s*[:：])$/u,
]

function suggestedPlatforms(goal) {
  const text = goal.trim().toLowerCase()
  // A heading or prior sentence can mark the final directive as illustrative
  // or rejected. Check the whole goal before taking a final clause.
  if (uncertainGoal.test(text) || nonInstructionContext.test(text)) return null
  // Other platform mentions also qualify the directive.
  const mentions = text.match(/\b(?:twitter|reddit)\b/gu) || []
  if (mentions.length < 1 || mentions.length > 2) return null
  const clause = text.split(/[.!;。！；\n]+/u).map(part => part.trim()).filter(Boolean).at(-1)
  const target = chosenPlatforms.exec(clause)
  if (!target) return null
  const [, both, first, second] = target
  if ((both && !second) || first === second || mentions.length !== (second ? 2 : 1)) return null
  const prefix = clause.slice(0, target.index).trim()
  if (!platformDirectives.some(directive => directive.test(prefix))) return null
  return new Set(second ? [first, second] : [first])
}

function suggest() {
  if (locked.value || !props.source || !requirement.value.trim()) return
  const platforms = suggestedPlatforms(requirement.value)
  if (platforms) {
    twitter.value = platforms.has('twitter')
    reddit.value = platforms.has('reddit')
  }
  suggestion.value = platforms ? 'suggested' : 'suggestionUnclear'
}

function options() { return preparationOptions({ types: selected.value.length ? [...selected.value] : null, max_agents: preparationInteger(maximum.value, 100, 1), seed: preparationInteger(seed.value, 4294967295), platforms: [...(twitter.value ? ['twitter'] : []), ...(reddit.value ? ['reddit'] : [])], max_rounds: preparationInteger(rounds.value, 24, 1), simulation_requirement: requirement.value }) }
async function review() {
  if (locked.value) return
  clearProfile()
  clearConfiguration()
  const life = lifetime, epoch = ++reviewEpoch
  plan.value = null; error.value = ''; notice.value = ''; suggestion.value = ''; pending.value = true
  emit('ready', null)
  try {
    const anchor = preparationSource(props.source), payload = { schema_version: 1, operation_id: newPreparationId(), source_revision: anchor.source_revision, options: options() }
    let source = anchor
    if (selectedExtras.value.length) {
      const extras = additionalSources.value.filter(item => selectedExtras.value.includes(item.source_revision)).map(preparationSource)
      if (extras.length !== selectedExtras.value.length || extras.length > 99) throw { code: 'invalid_request' }
      source = [anchor, ...extras]; payload.schema_version = 2; payload.source_revisions = source.map(item => item.source_revision)
    }
    const value = await props.methods.plan(JSON.parse(JSON.stringify(payload)), JSON.parse(JSON.stringify(source)))
    const admitted = await validatePreparationResult(value, { graph: props.displayGraphId, payload, source, planning: true })
    if (life !== lifetime || epoch !== reviewEpoch) return
    plan.value = admitted
  } catch (e) { if (life === lifetime && epoch === reviewEpoch) { if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) clear(); error.value = e?.code || 'invalid_request' } }
  finally { if (life === lifetime) { pending.value = false; await announce() } }
}
async function durableRequest(starting) {
  if (locked.value || starting && !canStart.value || !starting && !durable.value) return
  clearProfile()
  clearConfiguration()
  const life = lifetime, known = JSON.parse(JSON.stringify(starting ? plan.value : durable.value)), payload = preparationIdentity(known)
  if (starting) { durable.value = known; attempted.value = true }
  pending.value = true; error.value = ''; notice.value = ''
  try {
    const value = await (starting ? props.methods.start(payload) : props.methods.status(payload))
    const admitted = await validatePreparationResult(value, { graph: props.displayGraphId, payload, known })
    if (life !== lifetime) return
    durable.value = admitted
    if (plan.value?.operation_id === admitted.operation_id) plan.value = admitted
    if (admitted.state === 'ready') emit('ready', JSON.parse(JSON.stringify(admitted)))
  } catch (e) {
    if (life === lifetime) {
      if (['unauthorized', 'origin_denied', 'disconnected'].includes(e?.code)) { clear(); error.value = e.code }
      else { error.value = e?.code || 'transport_failure'; if (starting && !['model_calls_disabled', 'budget_denied'].includes(error.value)) notice.value = 'lost' }
    }
  } finally { if (life === lifetime) { pending.value = false; await announce() } }
}
async function inspectProfile(event) {
  const ready = profileReady.value, actor = profileActor.value
  if (locked.value || profilePending.value || configurationPending.value || !ready?.actors.some(item => item.source_entity_uuid === actor)) return
  clearProfile(false)
  const trigger = event.currentTarget
  const feedback = trigger.closest('.prepared-profiles')?.querySelector('.feedback')
  const owner = new AbortController()
  profileOwner = owner
  profilePending.value = true
  const current = () => props.connected && profileOwner === owner && !owner.signal.aborted &&
    profileReady.value === ready && profileActor.value === actor
  try {
    const selector = { ...preparationIdentity(ready), source_entity_uuid: actor }
    const value = await props.methods.profiles(selector, ready, { signal: owner.signal })
    if (current()) profileResult.value = value
  } catch (e) {
    if (current() && e?.code !== 'cancelled') profileError.value = e?.code || 'transport_failure'
  } finally {
    if (current()) {
      profilePending.value = false
      await nextTick()
      if (current() && (profileResult.value || profileError.value) &&
          (document.activeElement === trigger || document.activeElement === document.body)) feedback?.focus()
      if (profileOwner === owner) profileOwner = null
    }
  }
}
async function inspectConfiguration(event) {
  const ready = configurationReady.value
  if (locked.value || configurationPending.value || profilePending.value || !ready) return
  clearConfiguration()
  const trigger = event.currentTarget
  const feedback = trigger.closest('.prepared-configuration')?.querySelector('.feedback')
  const owner = new AbortController()
  configurationOwner = owner
  configurationPending.value = true
  const current = () => props.connected && configurationOwner === owner && !owner.signal.aborted &&
    configurationReady.value === ready
  try {
    const value = await props.methods.configuration(configurationIdentity(ready), ready, { signal: owner.signal })
    if (current()) configurationResult.value = value
  } catch (e) {
    if (current() && e?.code !== 'cancelled') configurationError.value = e?.code || 'transport_failure'
  } finally {
    if (current()) {
      configurationPending.value = false
      await nextTick()
      if (current() && (configurationResult.value || configurationError.value) &&
          (document.activeElement === trigger || document.activeElement === document.body)) feedback?.focus()
      if (configurationOwner === owner) configurationOwner = null
    }
  }
}
</script>
<template>
  <section class="preparation-workbench" :lang="locale" aria-labelledby="preparation-title">
    <h2 id="preparation-title">{{ copy.title }}</h2><p>{{ copy.intro }}</p>
    <p v-if="source" class="source"><strong>{{ copy.source }}</strong>: {{ source.source_name }}<br><span class="mono">{{ source.source_revision }}</span></p><p v-else>{{ copy.selectSource }}</p>
    <form @submit.prevent="review">
      <fieldset :disabled="locked || !source"><legend>{{ copy.review }}</legend>
        <fieldset class="preparation-sources" aria-describedby="preparation-sources-help"><legend>{{ copy.additionalSources }}</legend><p id="preparation-sources-help" class="limitations">{{ copy.sourcesHint }}</p><p v-if="!additionalSources.length" class="limitations">{{ copy.noAdditionalSources }}</p><label v-for="item in additionalSources" :key="item.source_revision" class="source-choice"><input v-model="selectedExtras" class="preparation-source-checkbox" type="checkbox" :value="item.source_revision" :disabled="selectedExtras.length >= 99 && !selectedExtras.includes(item.source_revision)"><span>{{ item.source_name }}<br><span class="mono">{{ item.source_revision }}</span></span></label></fieldset>
        <fieldset class="type-choices"><legend>{{ copy.types }}</legend><label v-for="name in labels" :key="name"><input v-model="selected" type="checkbox" :value="name">{{ name }}</label></fieldset>
        <div class="options"><label for="preparation-maximum">{{ copy.maximum }} (1–100)<input id="preparation-maximum" v-model="maximum" inputmode="numeric" type="text" required maxlength="3"></label><label for="preparation-seed">{{ copy.seed }} (0–4294967295)<input id="preparation-seed" v-model="seed" inputmode="numeric" type="text" required maxlength="10"></label><label for="preparation-rounds">{{ copy.rounds }} (1–24)<input id="preparation-rounds" v-model="rounds" inputmode="numeric" type="text" required maxlength="2"></label></div>
        <fieldset class="platforms"><legend>{{ copy.platforms }}</legend><label><input v-model="twitter" type="checkbox">Twitter</label><label><input v-model="reddit" type="checkbox">Reddit</label></fieldset>
        <label for="preparation-requirement">{{ copy.requirement }}<textarea id="preparation-requirement" v-model="requirement" rows="3" required maxlength="16384" aria-describedby="preparation-assumptions"></textarea></label>
        <div class="actions"><button class="primary review" type="submit">{{ copy.review }}</button><button class="suggest" type="button" :disabled="!requirement.trim()" @click="suggest">{{ copy.suggest }}</button></div>
      </fieldset>
    </form>
    <p id="preparation-assumptions" class="limitations">{{ copy.assumptions }}</p><p class="limitations">{{ copy.snapshot }}</p><p class="limitations">{{ copy.reservation }}</p><p class="limitations">{{ copy.disconnected }}</p>
    <p ref="feedback" class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1"><span v-if="pending">{{ copy.pending }}</span><span v-else-if="error">{{ preparationError(copy, error) }}</span><span v-if="suggestion && !pending && !error"> {{ copy[suggestion] }}</span><span v-if="notice"> {{ copy[notice] }}</span><span v-if="!pending && !error && plan"> {{ copy.states[plan.state] }}</span></p>
    <article v-for="record in records" :key="record.key" class="plan" :aria-label="record.key === 'durable' ? copy.recovered : copy.review">
      <h3>{{ record.key === 'durable' ? copy.recovered : copy.states[record.value.state] }}</h3>
      <details v-if="record.value.sources" class="frozen-sources"><summary>{{ copy.frozenSources }} ({{ record.value.sources.length }})</summary><ol><li v-for="item in record.value.sources" :key="item.source_revision"><strong>{{ item.source_name }}</strong><p class="mono">{{ item.source_revision }}<br>{{ item.source_sha256 }}</p></li></ol></details>
      <dl><dt>{{ copy.project }}</dt><dd class="mono">{{ record.value.scope.project_id }}</dd><dt>{{ copy.revision }}</dt><dd>{{ record.value.project_revision }}</dd><dt>{{ copy.source }}</dt><dd>{{ record.value.source.source_name }}<br><span class="mono">{{ record.value.source.source_revision }}<br>{{ record.value.source.source_sha256 }}</span></dd><dt>{{ copy.operation }}</dt><dd class="mono">{{ record.value.operation_id }}</dd><dt>{{ copy.projection }}</dt><dd class="mono">{{ record.value.projection_sha256 }}</dd><dt>{{ copy.digest }}</dt><dd class="mono">{{ record.value.plan_sha256 }}</dd><dt>{{ copy.ceiling }}</dt><dd>{{ record.value.authorization.ceiling_microusd || '—' }}</dd></dl>
      <p>{{ copy.progress }}: {{ copy.stages[record.value.progress.stage] }} · {{ record.value.progress.completed }} / {{ record.value.progress.total }}</p>
      <dl><dt>{{ copy.maximum }}</dt><dd>{{ record.value.options.max_agents }}</dd><dt>{{ copy.seed }}</dt><dd>{{ record.value.options.seed }}</dd><dt>{{ copy.platforms }}</dt><dd>{{ record.value.options.platforms.join(', ') }}</dd><dt>{{ copy.rounds }}</dt><dd>{{ record.value.options.max_rounds }}</dd><dt>{{ copy.requirement }}</dt><dd>{{ record.value.options.simulation_requirement }}</dd></dl>
      <p v-if="record.value.error_code">{{ preparationError(copy, record.value.error_code) }}</p>
      <details><summary>{{ copy.actors }} ({{ record.value.actors.length }})</summary><ol><li v-for="actor in record.value.actors" :key="actor.source_entity_uuid"><strong>{{ actor.name }}</strong><p>{{ actor.labels.join(', ') }}</p><p class="mono">{{ actor.source_entity_uuid }}</p></li></ol></details>
      <template v-if="record.value.receipt"><p class="ready">{{ copy.ready }}</p><dl><dt>{{ copy.simulation }}</dt><dd class="mono">{{ record.value.receipt.simulation_id }}</dd><dt>{{ copy.artifact }}</dt><dd class="mono">{{ record.value.receipt.artifact_sha256 }}</dd></dl><h4>{{ copy.files }}</h4><ul class="manifest"><li v-for="file in record.value.receipt.files" :key="file.name"><strong>{{ file.name }}</strong> · {{ file.size }} {{ copy.bytes }}<p class="mono">{{ file.sha256 }}</p></li></ul></template>
      <section v-if="record.value === configurationReady" class="prepared-configuration" aria-labelledby="prepared-configuration-title">
        <h4 id="prepared-configuration-title">{{ copy.configuration.title }}</h4>
        <div class="actions"><button class="inspect-configuration" type="button" :disabled="locked || configurationPending || profilePending" @click="inspectConfiguration">{{ copy.configuration.read }}</button></div>
        <p class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1">{{ configurationPending ? copy.pending : configurationError ? preparationError(copy, configurationError) : configurationResult ? copy.configuration.loaded : '' }}</p>
        <div v-if="configurationResult" class="prepared-configuration-result">
          <dl><dt>{{ copy.requirement }}</dt><dd class="configuration-goal">{{ configurationResult.goal }}</dd></dl>
          <h5>{{ copy.configuration.effective }}</h5>
          <dl class="effective-controls">
            <dt>{{ copy.platforms }}</dt><dd>{{ configurationResult.effective_controls.platforms.map(platformName).join(', ') }}</dd>
            <dt>{{ copy.seed }}</dt><dd>{{ configurationResult.effective_controls.seed }}</dd>
            <dt>{{ copy.rounds }}</dt><dd>{{ configurationResult.effective_controls.max_rounds }}</dd>
          </dl>
          <h5>{{ copy.configuration.proposals }}</h5>
          <h6>{{ copy.configuration.time }}</h6>
          <dl v-if="configurationResult.time_activity" class="proposed-time-activity">
            <template v-for="key in timeFields" :key="key">
              <dt>{{ copy.configuration[key] }}</dt><dd>{{ configurationResult.time_activity[key] }}</dd>
            </template>
            <template v-for="period in activityPeriods" :key="period">
              <dt>{{ copy.configuration[period] }} · {{ copy.configuration.hours }}</dt>
              <dd>{{ configurationResult.time_activity[period + '_hours'].join(', ') || copy.configuration.none }}</dd>
              <dt>{{ copy.configuration[period] }} · {{ copy.configuration.multiplier }}</dt>
              <dd>{{ configurationResult.time_activity[period + '_activity_multiplier'] }}</dd>
            </template>
          </dl>
          <p v-else>{{ copy.configuration.unavailable }}</p>
          <h6>{{ copy.configuration.topics }}</h6>
          <p v-if="configurationResult.hot_topics_omitted === null">{{ copy.configuration.unavailable }}</p>
          <template v-else>
            <ul v-if="configurationResult.hot_topics.length" class="proposed-hot-topics">
              <li v-for="(topic, index) in configurationResult.hot_topics" :key="index">{{ topic }}</li>
            </ul>
            <p v-else-if="configurationResult.hot_topics_omitted === 0">{{ copy.configuration.none }}</p>
            <p v-if="configurationResult.hot_topics_omitted > 0">{{ copy.configuration.omitted.replace('{count}', configurationResult.hot_topics_omitted) }}</p>
          </template>
          <h6>{{ copy.configuration.platformControls }}</h6>
          <p v-if="configurationResult.platform_controls === null">{{ copy.configuration.unavailable }}</p>
          <article v-for="control in configurationResult.platform_controls || []" :key="control.platform" class="proposed-platform-controls" :aria-label="platformName(control.platform)" :data-platform="control.platform">
            <h6>{{ platformName(control.platform) }}</h6>
            <dl>
              <template v-for="key in platformFields" :key="key">
                <dt>{{ copy.configuration[key] }}</dt><dd>{{ control[key] }}</dd>
              </template>
            </dl>
          </article>
        </div>
      </section>
      <section v-if="record.value === profileReady" class="prepared-profiles" aria-labelledby="prepared-profiles-title">
        <h4 id="prepared-profiles-title">{{ copy.profiles.title }}</h4>
        <label for="prepared-profile-actor">{{ copy.profiles.actor }}
          <select id="prepared-profile-actor" v-model="profileActor" :disabled="!connected || pending || (busy && !profilePending)">
            <option value="" disabled>{{ copy.profiles.choose }}</option>
            <option v-for="actor in profileReady.actors" :key="actor.source_entity_uuid" :value="actor.source_entity_uuid">{{ actor.name }} · {{ actor.labels.join(', ') }} · {{ actor.source_entity_uuid }}</option>
          </select>
        </label>
        <div class="actions"><button class="inspect-profile" type="button" :disabled="locked || profilePending || configurationPending || !profileActor" @click="inspectProfile">{{ copy.profiles.read }}</button></div>
        <p class="feedback" role="status" aria-live="polite" aria-atomic="true" tabindex="-1">{{ profilePending ? copy.pending : profileError ? preparationError(copy, profileError) : profileResult ? copy.profiles.loaded : '' }}</p>
        <div v-if="profileResult" class="prepared-profile-result">
          <h5 class="profile-name">{{ profileResult.actor.name }}</h5>
          <p class="profile-actor-labels">{{ profileResult.actor.labels.join(', ') }}</p>
          <p class="mono profile-actor-uuid">{{ profileResult.source_entity_uuid }}</p>
          <article v-for="profile in profileResult.generated_profiles" :key="profile.platform" class="saved-platform-profile" :data-platform="profile.platform">
            <h5>{{ profile.platform === 'twitter' ? 'Twitter' : 'Reddit' }}</h5>
            <dl><dt>{{ copy.profiles.handle }}</dt><dd class="profile-handle">{{ profile.handle }}</dd><dt>{{ copy.profiles.description }}</dt><dd class="profile-description">{{ profile.description }}</dd><dt>{{ copy.profiles.persona }}</dt><dd class="profile-persona">{{ profile.persona }}</dd></dl>
          </article>
          <h5>{{ copy.profiles.support }}</h5>
          <p v-if="profileResult.frozen_support.summary" class="profile-summary">{{ profileResult.frozen_support.summary }}</p>
          <ul class="profile-evidence"><li v-for="(id, index) in profileResult.frozen_support.evidence_ids" :key="index" class="mono">{{ id }}</li></ul>
          <ul><li v-for="(fact, index) in profileResult.frozen_support.facts" :key="index" class="profile-fact"><p>{{ fact.fact }}</p><ul><li v-for="(id, index) in fact.evidence_ids" :key="index" class="mono">{{ id }}</li></ul></li></ul>
        </div>
      </section>
    </article>
    <p v-if="plan?.state === 'planned' && (!plan.authorization.model_calls_enabled || !plan.authorization.ceiling_microusd)" class="limitations">{{ copy.disabled }}</p>
    <div class="actions"><button v-if="plan?.state === 'planned'" class="primary start" type="button" :disabled="!canStart" @click="durableRequest(true)">{{ copy.start }}</button><button v-if="durable" class="refresh" type="button" :disabled="locked" @click="durableRequest(false)">{{ copy.refresh }}</button><button class="clear" type="button" @click="clear">{{ copy.reset }}</button></div>
  </section>
</template>
<style scoped>
.preparation-workbench{background:var(--surface,#fff);color:var(--ink,#172638);border:1px solid var(--border,#b9c4d2);border-radius:8px;padding:24px;margin-bottom:24px;min-width:0;overflow-wrap:anywhere;font:16px/1.5 system-ui,sans-serif}.preparation-workbench *{box-sizing:border-box}h2{font-size:1.25rem;margin:0 0 16px}h3{font-size:1.1rem}p{margin:8px 0}fieldset{border:0;padding:0;margin:16px 0;min-width:0}legend{color:var(--muted,#526174)}label{display:flex;flex-direction:column;gap:6px;font-weight:550;min-width:0}.options{display:grid;gap:12px}.type-choices,.platforms{display:flex;flex-wrap:wrap;gap:8px 16px}.type-choices label,.platforms label{flex-direction:row;align-items:center;min-height:44px}input,textarea{min-width:0;width:100%;border:1px solid var(--control-border,#7b8797);border-radius:5px;min-height:44px;padding:10px 12px;font:inherit;background:var(--surface,#fff);color:inherit}input[type=checkbox]{width:20px;min-height:20px}textarea{resize:vertical}button{min-height:44px;padding:10px 16px;border:1px solid var(--border,#b9c4d2);border-radius:5px;font:inherit;color:var(--primary,#174d96);background:var(--surface,#fff);cursor:pointer;max-width:100%;overflow-wrap:anywhere}.primary{color:white;background:var(--primary,#174d96);border-color:var(--primary,#174d96)}button:disabled{opacity:.65;cursor:default}button:hover:not(:disabled){filter:brightness(.92)}:is(button,input,textarea,summary,.feedback):focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}.limitations{color:var(--muted,#526174);font-size:.875rem}.source,.plan{border-top:1px solid var(--border,#b9c4d2);padding-top:16px;margin-top:16px}.feedback{border-left:3px solid var(--primary,#174d96);padding:8px 12px}.feedback:empty{display:none}.mono{font:.875rem/1.6 ui-monospace,Consolas,monospace}dl{display:grid;gap:4px 16px}dt{font-weight:550}dd{margin:0 0 8px;min-width:0}summary{min-height:44px;padding:10px 0;cursor:pointer}li{margin:12px 0}.ready{border-left:3px solid var(--primary,#174d96);padding:8px 12px}@media(min-width:768px){.options{grid-template-columns:repeat(3,minmax(0,1fr))}dl{grid-template-columns:minmax(140px,1fr) minmax(0,3fr)}}@media(max-width:480px){.preparation-workbench{padding:16px}}
.source-choice{flex-direction:row;align-items:center;gap:12px;min-height:44px;margin:8px 0}.source-choice input{flex-shrink:0}.source-choice span{min-width:0}
.prepared-configuration,.prepared-profiles{border-top:1px solid var(--border,#b9c4d2);margin-top:16px;padding-top:16px}.prepared-profiles select{min-width:0;max-width:100%;width:100%;border:1px solid var(--control-border,#7b8797);border-radius:5px;min-height:44px;padding:8px 12px;font:inherit;background:var(--surface,#fff);color:inherit}.prepared-profiles select:focus-visible{outline:3px solid var(--primary,#174d96);outline-offset:3px}.profile-description,.profile-persona,.profile-summary{white-space:pre-wrap}
.configuration-goal,.proposed-hot-topics li{white-space:pre-wrap}
.prepared-configuration h5{font-size:1rem;margin:16px 0 8px}
.prepared-configuration h6{font-size:.9375rem;margin:12px 0 4px}
</style>
