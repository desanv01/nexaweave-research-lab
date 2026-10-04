<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { copyFor } from '../../i18n/workbench.js'
import { prepareDossierExport } from '../../api/dossierExport.js'
const props = defineProps({ result: Object, locale: { type: String, default: 'en' } })
const copy = computed(() => copyFor(props.locale).exports)
const feedback = ref('')
let ownedUrl = null, cleanupTimer = null
function cleanup() {
  if (cleanupTimer !== null) { clearTimeout(cleanupTimer); cleanupTimer = null }
  if (ownedUrl !== null) {
    const url = ownedUrl; ownedUrl = null
    try { URL.revokeObjectURL(url) } catch { /* Best effort browser resource cleanup. */ }
  }
}
watch(() => props.result, () => { cleanup(); feedback.value = '' }, { flush: 'sync' })
onBeforeUnmount(cleanup)
function download(format) {
  cleanup(); feedback.value = ''
  const prepared = prepareDossierExport(props.result, format)
  if (!prepared.ok) { feedback.value = prepared.code; return }
  let anchor = null
  try {
    ownedUrl = URL.createObjectURL(new Blob([prepared.bytes], { type: prepared.mime }))
    anchor = document.createElement('a')
    anchor.href = ownedUrl; anchor.download = prepared.filename
    anchor.hidden = true; document.body.append(anchor)
    anchor.click()
    feedback.value = 'requested'
    // The timer only revokes this owned URL; it never initiates a download.
    cleanupTimer = setTimeout(cleanup, 1000)
  } catch { cleanup(); feedback.value = 'failed' }
  finally { anchor?.remove() }
}
</script>
<template>
  <section v-if="result?.mode === 'model_free_evidence_dossier'" class="dossier-export" :aria-label="copy.title">
    <h3>{{ copy.title }}</h3>
    <p>{{ copy.help }}</p>
    <div class="export-actions"><button type="button" @click="download('markdown')">{{ copy.markdown }}</button><button type="button" @click="download('json')">{{ copy.json }}</button></div>
    <p role="status" aria-live="polite">{{ feedback ? copy[feedback] : '' }}</p>
  </section>
</template>
<style scoped>
.dossier-export{min-width:0;margin:16px 0 24px;padding:16px;border:1px solid var(--border);border-radius:8px;background:var(--surface)}h3{font-size:1rem;margin:0 0 8px}p{margin:8px 0;overflow-wrap:anywhere;line-height:1.5;color:var(--muted)}.export-actions{display:flex;flex-wrap:wrap;gap:8px}button{box-sizing:border-box;min-height:44px;max-width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:5px;background:var(--surface);color:var(--primary);font:inherit;cursor:pointer;overflow-wrap:anywhere}button:hover{background:var(--canvas)}button:focus-visible{outline:3px solid var(--primary);outline-offset:3px}
</style>
