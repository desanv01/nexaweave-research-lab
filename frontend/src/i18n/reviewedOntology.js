const copy = {
  en: {
    project: 'Saved project ontology',
    load: 'Load ontology', save: 'Save ontology', keepDraft: 'Keep draft', useSaved: 'Use saved version',
    initial: 'Load the project ontology to start editing.',
    loading: 'Loading project ontology…', saving: 'Saving project ontology…',
    loaded: revision => `Loaded project revision ${revision}.`,
    saved: revision => `Saved project revision ${revision}.`,
    empty: revision => `Project revision ${revision} has no saved ontology.`,
    conflict: 'The project revision may have changed. Load the current ontology to recover your draft.',
    unknown: 'The save could not be confirmed. Load the current ontology to recover your draft.',
    loadFailed: 'Could not load the project ontology. Try loading again.',
    choose: revision => `Project revision ${revision} differs from your draft. Choose which version to edit.`,
    invalid: 'Check the names, descriptions, attributes, examples, and relationship endpoints before saving.',
    limit: 'The ontology exceeds the save size limit.',
    exhausted: 'The project is at its maximum revision.',
    limits: 'Use up to 10 types of each kind. Each type requires 1–10 text attributes and a description of up to 100 characters. Relationships require 1–10 distinct endpoint pairs.',
    examples: 'Examples', addExample: 'Add example', removeExample: 'Remove example'
  },
  zh: {
    project: '已保存的项目本体',
    load: '加载本体', save: '保存本体', keepDraft: '保留草稿', useSaved: '使用已保存版本',
    initial: '加载项目本体后即可编辑。',
    loading: '正在加载项目本体…', saving: '正在保存项目本体…',
    loaded: revision => `已加载项目修订版 ${revision}。`,
    saved: revision => `已保存项目修订版 ${revision}。`,
    empty: revision => `项目修订版 ${revision} 尚未保存本体。`,
    conflict: '项目修订版可能已变更。请加载当前本体以恢复草稿。',
    unknown: '无法确认保存结果。请加载当前本体以恢复草稿。',
    loadFailed: '无法加载项目本体，请重新加载。',
    choose: revision => `项目修订版 ${revision} 与草稿不同。请选择要编辑的版本。`,
    invalid: '保存前请检查名称、描述、属性、示例和关系端点。',
    limit: '本体超出保存大小限制。',
    exhausted: '项目已达到最大修订版本号。',
    limits: '实体和关系各最多 10 种。每种须有 1–10 个文本属性，描述不超过 100 个字符。每个关系须有 1–10 对不同的端点。',
    examples: '示例', addExample: '添加示例', removeExample: '移除示例'
  },
  ms: {
    project: 'Ontologi projek yang disimpan',
    load: 'Muatkan ontologi', save: 'Simpan ontologi', keepDraft: 'Kekalkan draf', useSaved: 'Guna versi tersimpan',
    initial: 'Muatkan ontologi projek untuk mula menyunting.',
    loading: 'Memuatkan ontologi projek…', saving: 'Menyimpan ontologi projek…',
    loaded: revision => `Semakan projek ${revision} dimuatkan.`,
    saved: revision => `Semakan projek ${revision} disimpan.`,
    empty: revision => `Tiada ontologi tersimpan pada semakan projek ${revision}.`,
    conflict: 'Semakan projek mungkin telah berubah. Muatkan ontologi semasa untuk memulihkan draf anda.',
    unknown: 'Penyimpanan tidak dapat disahkan. Muatkan ontologi semasa untuk memulihkan draf anda.',
    loadFailed: 'Ontologi projek gagal dimuatkan. Cuba muatkan sekali lagi.',
    choose: revision => `Semakan projek ${revision} berbeza daripada draf anda. Pilih versi yang ingin disunting.`,
    invalid: 'Semak nama, penerangan, atribut, contoh dan titik hujung hubungan sebelum menyimpan.',
    limit: 'Ontologi melebihi had saiz penyimpanan.',
    exhausted: 'Projek telah mencapai semakan maksimum.',
    limits: 'Gunakan sehingga 10 jenis bagi setiap kategori. Setiap jenis memerlukan 1–10 atribut teks dan penerangan sehingga 100 aksara. Hubungan memerlukan 1–10 pasangan titik hujung yang unik.',
    examples: 'Contoh', addExample: 'Tambah contoh', removeExample: 'Buang contoh'
  }
}

export const reviewedOntologyCopyFor = locale => copy[locale] || copy.en
