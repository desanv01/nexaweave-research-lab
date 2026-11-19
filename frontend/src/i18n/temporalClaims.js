const temporalClaimMessages = {
  en: {
    history: 'Historical view of retained evidence',
    created_at: 'Recorded at',
    valid_at: 'Valid from',
    invalid_at: 'Valid until',
    expired_at: 'Invalidation recorded at',
  },
  zh: {
    history: '保留证据的历史视图',
    created_at: '记录时间',
    valid_at: '生效时间',
    invalid_at: '失效时间',
    expired_at: '失效记录时间',
  },
  ms: {
    history: 'Paparan sejarah bukti tersimpan',
    created_at: 'Masa direkodkan',
    valid_at: 'Sah mulai',
    invalid_at: 'Sah sehingga',
    expired_at: 'Masa ketidaksahan direkodkan',
  },
}

export function temporalClaimCopy(locale) {
  return temporalClaimMessages[locale] || temporalClaimMessages.en
}
