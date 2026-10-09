import { createI18n } from 'vue-i18n'
import { computed, watch } from 'vue'
import languages from '../../../locales/languages.json'

const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const key = path.match(/\/([^/]+)\.json$/)[1]
  if (languages[key]) {
    messages[key] = localeFiles[path].default
    availableLocales.push({ key, label: languages[key].label })
  }
}

// Storage is optional; refusing it must not disable language selection.
export function createLocaleController({ catalog = messages, storage = () => globalThis.localStorage, document = () => globalThis.document } = {}) {
  const supported = key => typeof key === 'string' && Object.hasOwn(catalog, key) && Object.hasOwn(languages, key)
  const fallback = supported('zh') ? 'zh' : Object.keys(catalog).find(supported)
  let saved
  try { saved = storage()?.getItem('locale') } catch { /* Use the supported default. */ }
  const i18n = createI18n({ legacy: false, locale: supported(saved) ? saved : fallback, fallbackLocale: fallback, messages: catalog })
  const setLocale = key => { if (!supported(key)) return false; i18n.global.locale.value = key; return true }
  const stop = watch(i18n.global.locale, key => {
    if (!supported(key)) { i18n.global.locale.value = fallback; return }
    try { storage()?.setItem('locale', key) } catch { /* Keep the in-memory selection. */ }
    const root = document()?.documentElement
    if (root) root.lang = key
  }, { immediate: true, flush: 'sync' })
  const locale = computed({ get: () => i18n.global.locale.value, set: setLocale })
  return { i18n, locale, setLocale, dispose: stop }
}
const shared = createLocaleController()
const i18n = shared.i18n
export const uiLocale = shared.locale
export const setUiLocale = shared.setLocale

export { availableLocales }
export default i18n
