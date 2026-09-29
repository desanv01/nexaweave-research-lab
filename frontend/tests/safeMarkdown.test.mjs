import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { JSDOM } from 'jsdom'
import createDOMPurify from 'dompurify'

import {
  MAX_MARKDOWN_CHARS,
  createSafeMarkdownRenderer,
  renderSafeMarkdown
} from '../src/utils/safeMarkdown.js'

function fixture() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>')
  const render = createSafeMarkdownRenderer(dom.window)
  const inspect = (content, options) => {
    dom.window.document.body.innerHTML = render(content, options)
    return dom.window.document.body
  }
  return { dom, render, inspect }
}

const allowedTags = new Set([
  'A', 'BLOCKQUOTE', 'BR', 'CODE', 'DEL', 'EM', 'H2', 'H3', 'H4', 'H5',
  'H6', 'HR', 'LI', 'OL', 'P', 'PRE', 'S', 'STRONG', 'TABLE', 'TBODY',
  'TD', 'TH', 'THEAD', 'TR', 'UL'
])
const allowedClasses = new Set([
  'code-block', 'inline-code', 'md-h2', 'md-h3', 'md-h4', 'md-h5',
  'md-quote', 'md-li', 'md-oli', 'md-ul', 'md-ol', 'md-hr', 'md-p'
])

function assertSafeTree(root) {
  const prohibited = 'script,img,iframe,object,embed,svg,math,form,style,link,video,audio,source,canvas'
  assert.equal(root.querySelector(prohibited), null)
  for (const element of root.querySelectorAll('*')) {
    assert.ok(allowedTags.has(element.tagName), element.outerHTML)
    for (const attribute of element.attributes) {
      assert.ok(
        ['class', 'data-level', 'start', 'href', 'title'].includes(attribute.name),
        element.outerHTML
      )
      if (attribute.name === 'class') {
        assert.ok(allowedClasses.has(attribute.value), element.outerHTML)
      }
      if (attribute.name === 'data-level') {
        assert.match(attribute.value, /^(?:[0-9]|[12][0-9]|3[0-2])$/)
        assert.equal(element.tagName, 'LI')
      }
      if (attribute.name === 'start') {
        assert.match(attribute.value, /^(?:0|[1-9]\d{0,9})$/)
        assert.equal(element.tagName, 'OL')
      }
      if (attribute.name === 'href') {
        assert.equal(element.tagName, 'A')
        const url = new URL(attribute.value)
        assert.ok(['http:', 'https:'].includes(url.protocol))
        assert.ok(url.hostname)
      }
    }
  }
}

test('benign Markdown retains semantics, fixed classes and actual list starts', () => {
  const { inspect } = fixture()
  const root = inspect(
    '# Title\n\n**bold** *em* `<tag>`\n\n> quote\n\n' +
    '- parent\n  - child\n\n3. third\n4. fourth\n\n' +
    'Interlude\n\n9. separate\n\n---\n\n| Left | Right |\n| --- | --- |\n| 雪 | café |'
  )
  assert.equal(root.querySelector('h2.md-h2')?.textContent, 'Title')
  assert.equal(root.querySelector('strong')?.textContent, 'bold')
  assert.equal(root.querySelector('em')?.textContent, 'em')
  assert.equal(root.querySelector('code.inline-code')?.textContent, '<tag>')
  assert.equal(root.querySelector('blockquote.md-quote')?.textContent.trim(), 'quote')
  assert.equal(root.querySelector('ul.md-ul ul.md-ul li.md-li')?.dataset.level, '1')
  const ordered = [...root.querySelectorAll('ol.md-ol')]
  assert.equal(ordered[0]?.getAttribute('start'), '3')
  assert.equal(ordered[1]?.getAttribute('start'), '9')
  assert.equal(root.querySelector('table td')?.textContent, '雪')
  assert.ok(root.querySelector('hr.md-hr'))
  assertSafeTree(root)
})

test('section heading suppression is opt-in and does not strip fenced code', () => {
  const { inspect } = fixture()
  const markdown = '## Repeated section\n\nParagraph'
  assert.equal(inspect(markdown).querySelector('h3.md-h3')?.textContent, 'Repeated section')
  assert.equal(
    inspect(markdown, { stripLeadingSectionHeading: true }).querySelector('h3'),
    null
  )
  assert.match(inspect(markdown, { stripLeadingSectionHeading: true }).textContent, /Paragraph/)
  const fenced = '```md\n## literal heading\n```\n\nParagraph'
  const root = inspect(fenced, { stripLeadingSectionHeading: true })
  assert.match(root.querySelector('pre.code-block')?.textContent ?? '', /## literal heading/)
  assertSafeTree(root)
})

test('raw HTML and code remain visible text, while images never load', () => {
  const { inspect } = fixture()
  const root = inspect(
    '<img src="https://remote.invalid/pixel" onerror="run()">\n\n' +
    '![remote alt](https://remote.invalid/pixel)\n\n' +
    '```html\n</code><script>run()</script>\n```'
  )
  assert.match(root.textContent, /remote alt/)
  assert.match(root.textContent, /<img src=/)
  assert.match(root.querySelector('pre')?.textContent ?? '', /<script>run\(\)<\/script>/)
  assertSafeTree(root)
})

test('only explicit http and https links become active', () => {
  const { inspect } = fixture()
  const root = inspect(
    '[good](https://example.com/path "title") [plain](http://example.org) ' +
    '[js](javascript:alert(1)) [encoded](javascript&#58;alert(1)) ' +
    '[relative](/local) [network](//example.com) [data](data:text/html,hi) ' +
    '[file](file:///etc/passwd) [vb](vbscript:msgbox(1)) ' +
    '[control](java%0ascript:alert(1))'
  )
  assert.equal(root.querySelectorAll('a').length, 2)
  assert.equal(root.querySelector('a')?.getAttribute('href'), 'https://example.com/path')
  assert.match(root.textContent, /js/)
  assert.match(root.textContent, /relative/)
  assertSafeTree(root)
})

test('malicious and incomplete Markdown produces only allowed DOM', () => {
  const payloads = [
    '<script>alert(1)</script><img src=x onerror=alert(1)>',
    '<svg><a xlink:href="javascript:alert(1)">svg</a></svg>',
    '<math><mtext><table><mglyph onload=alert(1)>',
    '<style>@import url(https://remote.invalid/a.css)</style>',
    '<iframe src="https://remote.invalid"></iframe><object data="x"></object>',
    '<form action="https://remote.invalid"><input autofocus onfocus=alert(1)>',
    '<a id="location" name="constructor" href="javascript:alert(1)">clobber</a>',
    '<p class="x" style="background:url(https://remote.invalid)" onclick="run()">text',
    '```\n</pre><img src=x onerror=run()>\n```',
    '[quote](https://example.com/" onclick="run())',
    '[unfinished](https://example.com',
    '![alt](https://remote.invalid/image.png)',
    '<img src=x onerror="&quot;><svg onload=run()>"'
  ]
  const { inspect } = fixture()
  for (const payload of payloads) {
    const root = inspect(payload)
    assertSafeTree(root)
    assert.ok(root.textContent.length > 0, payload)
  }
})

test('empty, nonstring, exact cap, over cap and unsupported DOM are bounded', () => {
  const { render, inspect } = fixture()
  let coercions = 0
  const object = { toString() { coercions++; return '<img src=x>' } }
  for (const value of [null, undefined, 0, false, object]) {
    assert.equal(render(value), '')
  }
  assert.equal(coercions, 0)
  assert.equal(render(''), '')
  const exact = '雪'.repeat(MAX_MARKDOWN_CHARS)
  assert.equal(inspect(exact).querySelector('p.md-p')?.textContent, exact)
  const over = inspect('x'.repeat(MAX_MARKDOWN_CHARS + 1))
  assert.equal(over.textContent, '[Content too long to display]')
  assertSafeTree(over)
  assert.equal(createSafeMarkdownRenderer(null)('**unsafe**'), '')
  assert.equal(createSafeMarkdownRenderer({ document: {} })('unsafe'), '')
  assert.equal(renderSafeMarkdown('browser-only'), '')
})

test('each renderer has private hooks and does not alter other sanitizer instances', () => {
  const { dom, render } = fixture()
  assert.match(render('**safe**'), /strong/)
  const independent = createDOMPurify(dom.window)
  const unrelated = independent.sanitize('<p class="unrelated">visible</p>')
  assert.match(unrelated, /class="unrelated"/)
  const another = fixture()
  assert.match(another.render('[link](https://example.com)'), /href=/)
  assert.match(render('[link](https://example.com)'), /href=/)
})

test('all report and chat HTML sinks are wired directly to safe renderer', () => {
  const step4 = readFileSync(
    fileURLToPath(new URL('../src/components/Step4Report.vue', import.meta.url)),
    'utf8'
  )
  const step5 = readFileSync(
    fileURLToPath(new URL('../src/components/Step5Interaction.vue', import.meta.url)),
    'utf8'
  )
  for (const source of [step4, step5]) {
    assert.doesNotMatch(source, /const renderMarkdown\s*=/)
    assert.doesNotMatch(source, /v-html="renderMarkdown\(/)
    assert.match(source, /v-html="renderSafeMarkdown\(/)
    for (const match of source.matchAll(/innerHTML:\s*([^\n]+)/g)) {
      assert.match(match[1], /^renderSafeMarkdown\(/)
    }
  }
  assert.match(step4, /isPlaceholder \? answerText : null/)
  assert.match(step4, /innerHTML: renderSafeMarkdown\(formatAnswer\(/)
  assert.match(step5, /v-html="renderSafeMarkdown\(msg\.content\)"/)
  assert.match(step5, /v-html="renderSafeMarkdown\(result\.answer\)"/)
  assert.doesNotMatch(step5, /counter-(?:reset|increment): list-counter/)
  assert.match(step5, /\.message-text :deep\(\.md-ol\) \{\s*list-style: decimal/)
})
