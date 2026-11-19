const messages = {
  en: {
    download: 'Download survey results (JSON)',
    exportTooLarge: 'These survey results exceed the JSON download limit.',
    exportFailed: 'The survey results download could not start.',
    missing: 'No reply available',
    unknownPlatform: 'Platform unavailable',
    actor: id => `Agent ${id}`
  },
  zh: {
    download: '下载问卷结果（JSON）',
    exportTooLarge: '这些问卷结果超出了 JSON 下载大小限制。',
    exportFailed: '无法开始下载问卷结果。',
    missing: '未收到可显示的回复',
    unknownPlatform: '平台不明',
    actor: id => `智能体 ${id}`
  },
  ms: {
    download: 'Muat turun hasil tinjauan (JSON)',
    exportTooLarge: 'Hasil tinjauan ini melebihi had muat turun JSON.',
    exportFailed: 'Muat turun hasil tinjauan tidak dapat dimulakan.',
    missing: 'Tiada jawapan tersedia',
    unknownPlatform: 'Platform tidak diketahui',
    actor: id => `Ejen ${id}`
  }
}

export const guidedSurveyCopyFor = locale => Object.hasOwn(messages, locale) ? messages[locale] : messages.en
