const copy = {
  en: {
    title: 'Inspect source revision',
    hint: 'Enter the saved revision UUID to open the retained text and passages.',
    invalid: 'Enter a source revision UUID in lowercase.',
    notFound: 'This source revision was not found in the current connection.'
  },
  zh: {
    title: '查看源材料修订版',
    hint: '输入已保存的修订版 UUID，以查看保留的文本和片段。',
    invalid: '请输入小写的源材料修订版 UUID。',
    notFound: '在当前连接中找不到此源材料修订版。'
  },
  ms: {
    title: 'Periksa semakan sumber',
    hint: 'Masukkan UUID semakan yang disimpan untuk membuka teks dan petikan tersimpan.',
    invalid: 'Masukkan UUID semakan sumber dalam huruf kecil.',
    notFound: 'Semakan sumber ini tidak ditemui dalam sambungan semasa.'
  }
}

export const sourceRevisionRecoveryCopyFor = locale => Object.hasOwn(copy, locale) ? copy[locale] : copy.en
