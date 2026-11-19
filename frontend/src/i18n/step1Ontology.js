const copy = {
  en: {
    entity: 'ENTITY',
    relation: 'RELATION',
    attributes: 'ATTRIBUTES',
    examples: 'EXAMPLES',
    connections: 'CONNECTIONS',
    entityTypes: 'GENERATED ENTITY TYPES',
    relationTypes: 'GENERATED RELATION TYPES',
    dashboard: 'SYSTEM DASHBOARD',
    close: 'Close ontology details'
  },
  zh: {
    entity: '实体',
    relation: '关系',
    attributes: '属性',
    examples: '示例',
    connections: '关联',
    entityTypes: '生成的实体类型',
    relationTypes: '生成的关系类型',
    dashboard: '系统仪表板',
    close: '关闭本体详情'
  },
  ms: {
    entity: 'ENTITI',
    relation: 'HUBUNGAN',
    attributes: 'ATRIBUT',
    examples: 'CONTOH',
    connections: 'SAMBUNGAN',
    entityTypes: 'JENIS ENTITI YANG DIJANA',
    relationTypes: 'JENIS HUBUNGAN YANG DIJANA',
    dashboard: 'PAPAN PEMUKA SISTEM',
    close: 'Tutup butiran ontologi'
  }
}

export const step1OntologyCopyFor = locale => Object.hasOwn(copy, locale) ? copy[locale] : copy.en
