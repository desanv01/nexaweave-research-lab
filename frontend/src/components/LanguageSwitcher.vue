<template>
  <div class="language-switcher" ref="switcherRef" @keydown.esc.stop.prevent="close(true)" @focusout="onFocusOut">
    <button ref="trigger" class="switcher-trigger" type="button" :aria-label="label" aria-haspopup="menu" :aria-expanded="open" @click="toggleDropdown" @keydown.down.prevent="showMenu" @keydown.up.prevent="showMenu">
      {{ currentLabel }}
      <span class="caret" aria-hidden="true">{{ open ? '▲' : '▼' }}</span>
    </button>
    <ul v-if="open" class="switcher-dropdown" role="menu" :aria-label="label" @keydown="moveFocus">
      <li v-for="loc in availableLocales" :key="loc.key" role="none"><button
        class="switcher-option"
        type="button" role="menuitemradio" :aria-checked="loc.key === locale" tabindex="-1"
        :class="{ active: loc.key === locale }"
        @click="switchLocale(loc.key)"
      >
        {{ loc.label }}
      </button></li>
    </ul>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import i18n, { availableLocales, uiLocale as locale, setUiLocale } from '@/i18n/index.js'
const open = ref(false)
const switcherRef = ref(null)
const trigger = ref(null)
const label = computed(() => i18n.global.t('common.language'))

const currentLabel = computed(() => {
  const found = availableLocales.find(l => l.key === locale.value)
  return found ? found.label : locale.value
})

const close = (restore = false) => { open.value = false; if (restore) trigger.value?.focus() }
const showMenu = async () => {
  open.value = true; await nextTick()
  switcherRef.value?.querySelector('[aria-checked="true"]')?.focus()
}
const toggleDropdown = () => { if (open.value) close(); else void showMenu() }
const moveFocus = event => {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const options = [...switcherRef.value.querySelectorAll('[role="menuitemradio"]')]
  const current = options.indexOf(document.activeElement)
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
  options[index]?.focus()
}
const onFocusOut = event => { if (!switcherRef.value?.contains(event.relatedTarget)) close() }

const switchLocale = (key) => {
  setUiLocale(key)
  close(true)
}

const onClickOutside = (e) => {
  if (switcherRef.value && !switcherRef.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
})
</script>

<style scoped>
.language-switcher {
  position: relative;
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
}

/* Light theme (default - for white header backgrounds) */
.switcher-trigger {
  min-height: 44px;
  background: transparent;
  color: #333;
  border: 1px solid #CCC;
  padding: 4px 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: border-color 0.2s, opacity 0.2s;
}

.switcher-trigger:hover {
  border-color: #999;
}

.caret {
  font-size: 0.6rem;
}

.switcher-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  background: #FFFFFF;
  border: 1px solid #DDD;
  list-style: none;
  padding: 4px 0;
  min-width: 100%;
  z-index: 1000;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.switcher-option {
  display: block;
  width: 100%;
  min-height: 44px;
  border: 0;
  background: transparent;
  text-align: left;
  font-family: inherit;
  padding: 6px 12px;
  font-size: 0.8rem;
  color: #333;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.switcher-option:hover {
  background: #F0F0F0;
}

.switcher-option.active {
  color: var(--orange, #FF4500);
}
.switcher-trigger:focus-visible,.switcher-option:focus-visible { outline: 2px solid var(--orange, #FF4500); outline-offset: 2px; }


</style>
