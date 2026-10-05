import { validateResult } from './workbench.js'

export const DOSSIER_EXPORT_LIMIT = 4 * 1024 * 1024
const formats = Object.freeze({
  markdown: { filename: 'nexaweave-evidence-dossier.md', mime: 'text/markdown;charset=utf-8' },
  json: { filename: 'nexaweave-evidence-dossier.json', mime: 'application/json;charset=utf-8' }
})

// Local DTO admission is not authentication or fingerprint verification.
export function prepareDossierExport(result, format) {
  try {
    if (!Object.hasOwn(formats, format)) throw new Error()
    const graph = result?.request?.display_graph_ids?.[0]
    validateResult('dossier', result, graph)
    const snapshot = JSON.parse(JSON.stringify(result))
    validateResult('dossier', snapshot, snapshot.request.display_graph_ids[0])
    const text = format === 'markdown' ? snapshot.markdown : JSON.stringify(snapshot) + '\n'
    const bytes = new TextEncoder().encode(text)
    if (bytes.byteLength > DOSSIER_EXPORT_LIMIT) return { ok: false, code: 'tooLarge' }
    return { ok: true, bytes, ...formats[format] }
  } catch { return { ok: false, code: 'invalid' } }
}
