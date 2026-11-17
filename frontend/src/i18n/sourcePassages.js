const sourcePassageCopy = {
  en: {
    selection: 'Retained passage',
    choose: 'Choose one passage',
    position: (ordinal, count) => `Passage ${ordinal} of ${count}`
  },
  zh: {
    selection: '保留片段',
    choose: '请选择一个片段',
    position: (ordinal, count) => `第 ${ordinal} 个片段，共 ${count} 个`
  },
  ms: {
    selection: 'Petikan tersimpan',
    choose: 'Pilih satu petikan',
    position: (ordinal, count) => `Petikan ${ordinal} daripada ${count}`
  }
}

export const sourcePassageCopyFor = locale => Object.hasOwn(sourcePassageCopy, locale)
  ? sourcePassageCopy[locale]
  : sourcePassageCopy.en
