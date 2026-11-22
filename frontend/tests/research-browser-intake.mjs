import { open } from 'node:fs/promises'
import path from 'node:path'

const formats = [
  ['original.pdf', 'pdf', 'application/pdf'],
  ['notes.md', 'text', 'text/markdown'],
  ['notes.txt', 'text', 'text/plain'],
  ['office.docx', 'docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
]

export function checkConnectSources(command, twitterOnly, { keys, integer, digest, uuid, text, requireTrue }) {
  if (!Object.hasOwn(command, 'intake')) {
    requireTrue(Array.isArray(command.sources) && command.sources.length === 4)
    for (const item of command.sources) {
      keys(item, ['source_revision', 'source_name']); uuid(item.source_revision); text(item.source_name, 1024)
    }
    requireTrue(new Set(command.sources.map(s => s.source_revision)).size === 4)
    keys(command.pdf, ['source_revision', 'size', 'sha256'])
    uuid(command.pdf.source_revision); integer(command.pdf.size, 2097152, 1); digest(command.pdf.sha256)
    requireTrue(command.sources.some(s => s.source_revision === command.pdf.source_revision))
    return
  }
  requireTrue(!twitterOnly && !Object.hasOwn(command, 'sources') && !Object.hasOwn(command, 'pdf'))
  requireTrue(Array.isArray(command.intake) && command.intake.length === formats.length)
  for (const [index, file] of command.intake.entries()) {
    keys(file, ['name', 'size', 'sha256'])
    requireTrue(file.name === formats[index][0])
    integer(file.size, 4500, 1)
    digest(file.sha256)
  }
}

export async function retainBrowserFiles(command, { page, exchange, sourceRoute, enabled, physical, sha, uuid, digest, requireTrue }) {
  const root = path.resolve(command.profile_root, '..', '..', 'uploaded-documents')
  await physical(root, true)
  const library = page.locator('.source-library')
  const form = library.locator('form.source-form').filter({ has: page.locator('#source-name') })
  const receipts = []
  let pdf
  for (const [index, spec] of command.intake.entries()) {
    const [name, format, mimeType] = formats[index]
    const filename = path.join(root, name)
    requireTrue((await physical(filename)).size === spec.size)
    const handle = await open(filename, 'r')
    let buffer
    try {
      buffer = Buffer.alloc(spec.size)
      let offset = 0
      while (offset < buffer.length) {
        const { bytesRead } = await handle.read(buffer, offset, buffer.length - offset, offset)
        requireTrue(bytesRead > 0)
        offset += bytesRead
      }
      requireTrue((await handle.stat()).size === spec.size && sha(buffer) === spec.sha256)
    } finally { await handle.close() }

    await form.locator('input[value="file"]').check({ timeout: 15000 })
    await form.locator('#source-file').setInputFiles({ name, mimeType, buffer }, { timeout: 15000 })
    await form.locator('#source-name').fill(name, { timeout: 15000 })
    await form.locator('button[type="submit"]').click({ timeout: 15000 })
    const prepared = library.locator('.prepared')
    const original = format === 'pdf'
    const retain = prepared.locator(original ? 'button:not(.primary)' : 'button.primary')
    await enabled(retain)
    const [inputDigest, revision] = await prepared.locator('dd.mono').allTextContents()
    uuid(revision)
    requireTrue(inputDigest === spec.sha256 && !receipts.some(receipt => receipt.source_revision === revision))

    const observed = await exchange(page, sourceRoute(original ? 'retain-original' : 'retain'), 'POST',
      () => retain.click({ timeout: 15000 }), 262144)
    const { value, request } = observed
    requireTrue(request.schema_version === (original ? 2 : 1) && request.source_name === name &&
      request.format === format && request.source_revision === revision && request.input_sha256 === spec.sha256)
    requireTrue(value.source.source_revision === revision && value.source.source_name === name &&
      value.extraction.format === format && value.extraction.input_sha256 === spec.sha256 &&
      value.extraction.input_hash_verified === true && value.graph_ingestion_executed === false &&
      value.binary_retained === original)
    digest(value.source.text_sha256)
    const receipt = library.locator('article.receipt')
    await receipt.getByText(revision, { exact: true }).waitFor({ state: 'visible', timeout: 15000 })
    const metadata = receipt.locator(':scope > dl').first()
    requireTrue(await metadata.getByText(value.source.text_sha256, { exact: true }).count() === 1)
    receipts.push({ source_revision: revision, source_name: name, source_sha256: value.source.text_sha256 })
    if (original) {
      requireTrue(value.binary.sha256 === spec.sha256 && value.binary.byte_length === spec.size)
      pdf = { source_revision: revision, size: spec.size, sha256: spec.sha256 }
    }
  }
  return { sources: receipts.map(({ source_revision, source_name }) => ({ source_revision, source_name })), pdf, receipts }
}
