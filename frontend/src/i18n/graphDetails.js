const graphDetailsCopy = {
  en: {
    name: 'Name:',
    uuid: 'UUID:',
    created: 'Created:',
    properties: 'Properties:',
    summary: 'Summary:',
    labels: 'Labels:',
    fact: 'Fact:',
    type: 'Type:',
    episodes: 'Episodes:',
    label: 'Label:',
    validFrom: 'Valid From:',
    selfRelations: 'Self Relations',
    selfRelationCount: count => `${count} ${count === 1 ? 'item' : 'items'}`,
    related: 'RELATED',
    relatedTo: 'RELATED_TO',
    unnamed: 'Unnamed',
    entityTypes: 'Entity Types',
    showEdgeLabels: 'Show Edge Labels'
  },
  zh: {
    panelTitle: '图谱关系可视化',
    nodeDetails: '节点详情',
    relationship: '关系详情',
    name: '名称：',
    uuid: 'UUID：',
    created: '创建时间：',
    properties: '属性：',
    summary: '摘要：',
    labels: '标签：',
    fact: '事实：',
    type: '类型：',
    episodes: '情节：',
    label: '标签：',
    validFrom: '生效时间：',
    selfRelations: '自环关系',
    selfRelationCount: count => `${count} 项`,
    related: '相关',
    relatedTo: '关联到',
    unnamed: '未命名',
    entityTypes: '实体类型',
    showEdgeLabels: '显示边标签'
  },
  ms: {
    name: 'Nama:',
    uuid: 'UUID:',
    created: 'Dicipta pada:',
    properties: 'Atribut:',
    summary: 'Ringkasan:',
    labels: 'Label:',
    fact: 'Fakta:',
    type: 'Jenis:',
    episodes: 'Episod:',
    label: 'Label:',
    validFrom: 'Sah mulai:',
    selfRelations: 'Hubungan kendiri',
    selfRelationCount: count => `${count} item`,
    related: 'Berkaitan',
    relatedTo: 'Berkaitan dengan',
    unnamed: 'Tanpa nama',
    entityTypes: 'Jenis entiti',
    showEdgeLabels: 'Paparkan label sisi'
  }
}

export const graphDetailsCopyFor = locale => Object.hasOwn(graphDetailsCopy, locale)
  ? graphDetailsCopy[locale]
  : graphDetailsCopy.en
