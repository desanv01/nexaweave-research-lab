/** Bounded Markdown rendering for report and interaction HTML sinks. */

import MarkdownIt from 'markdown-it'
import createDOMPurify from 'dompurify'

export const MAX_MARKDOWN_CHARS = 262144

const TOO_LONG_HTML = '<p class="md-p">[Content too long to display]</p>'
const ALLOWED_TAGS = [
  'a', 'blockquote', 'br', 'code', 'del', 'em', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hr', 'li', 'ol', 'p', 'pre', 's', 'strong', 'table', 'tbody', 'td', 'th',
  'thead', 'tr', 'ul'
]
const ALLOWED_ATTR = ['class', 'data-level', 'href', 'start', 'title']
const FIXED_CLASSES = Object.freeze({
  h2: ['md-h2'],
  h3: ['md-h3'],
  h4: ['md-h4'],
  h5: ['md-h5'],
  blockquote: ['md-quote'],
  li: ['md-li', 'md-oli'],
  ul: ['md-ul'],
  ol: ['md-ol'],
  hr: ['md-hr'],
  p: ['md-p'],
  pre: ['code-block'],
  code: ['inline-code']
})

function isAllowedHttpUrl(value) {
  if (typeof value !== 'string' || !/^https?:\/\//i.test(value)) return false
  if (/[\u0000-\u0020\u007f]/u.test(value)) return false
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
      Boolean(parsed.hostname) &&
      parsed.username === '' &&
      parsed.password === ''
  } catch {
    return false
  }
}

function buildMarkdownParser() {
  const markdown = new MarkdownIt({
    html: false,
    breaks: true,
    linkify: false,
    typographer: false,
    maxNesting: 32
  })
  markdown.validateLink = isAllowedHttpUrl
  const escape = markdown.utils.escapeHtml
  markdown.renderer.rules.image = (tokens, index) => escape(tokens[index].content)
  markdown.renderer.rules.code_inline = (tokens, index) =>
    '<code class="inline-code">' + escape(tokens[index].content) + '</code>'
  const codeBlock = (tokens, index) =>
    '<pre class="code-block"><code>' + escape(tokens[index].content) + '</code></pre>\n'
  markdown.renderer.rules.fence = codeBlock
  markdown.renderer.rules.code_block = codeBlock
  return markdown
}

function decorateTokens(tokens) {
  const listStack = []
  const headings = { h1: 'h2', h2: 'h3', h3: 'h4', h4: 'h5', h5: 'h6', h6: 'h6' }
  for (const token of tokens) {
    if (token.type === 'heading_open' || token.type === 'heading_close') {
      token.tag = headings[token.tag] || 'h6'
      if (token.type === 'heading_open' && ['h2', 'h3', 'h4', 'h5'].includes(token.tag)) {
        token.attrSet('class', 'md-' + token.tag)
      }
    } else if (token.type === 'blockquote_open') {
      token.attrSet('class', 'md-quote')
    } else if (token.type === 'paragraph_open') {
      token.attrSet('class', 'md-p')
    } else if (token.type === 'bullet_list_open') {
      token.attrSet('class', 'md-ul')
      listStack.push('ul')
    } else if (token.type === 'ordered_list_open') {
      token.attrSet('class', 'md-ol')
      listStack.push('ol')
    } else if (token.type === 'list_item_open') {
      token.attrSet('class', listStack[listStack.length - 1] === 'ol' ? 'md-oli' : 'md-li')
      token.attrSet('data-level', String(Math.max(0, listStack.length - 1)))
    } else if (token.type === 'bullet_list_close' || token.type === 'ordered_list_close') {
      listStack.pop()
    } else if (token.type === 'hr') {
      token.attrSet('class', 'md-hr')
    }
  }
}

function installPrivateAttributePolicy(purifier) {
  purifier.addHook('uponSanitizeAttribute', (node, data) => {
    const tag = node.localName?.toLowerCase()
    const name = data.attrName?.toLowerCase()
    const value = data.attrValue
    if (name === 'class') {
      data.keepAttr = Boolean(FIXED_CLASSES[tag]?.includes(value))
    } else if (name === 'href') {
      data.keepAttr = tag === 'a' && isAllowedHttpUrl(value)
    } else if (name === 'title') {
      data.keepAttr = tag === 'a' && typeof value === 'string' && value.length <= 512
    } else if (name === 'start') {
      data.keepAttr = tag === 'ol' && /^(?:0|[1-9]\d{0,9})$/.test(value) &&
        Number(value) <= 2147483647
    } else if (name === 'data-level') {
      data.keepAttr = tag === 'li' && /^(?:[0-9]|[12][0-9]|3[0-2])$/.test(value)
    } else {
      data.keepAttr = false
    }
  })
}

/** Return a renderer bound to one private DOMPurify instance. */
export function createSafeMarkdownRenderer(windowLike) {
  if (!windowLike?.document?.createElement) return () => ''
  let purifier
  try {
    purifier = createDOMPurify(windowLike)
    if (!purifier?.isSupported || typeof purifier.sanitize !== 'function') return () => ''
    installPrivateAttributePolicy(purifier)
  } catch {
    return () => ''
  }

  const markdown = buildMarkdownParser()
  const sanitize = (html) => purifier.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM: false,
    RETURN_DOM_FRAGMENT: false
  })

  return (content, options = {}) => {
    if (typeof content !== 'string' || content.length === 0) return ''
    try {
      if (content.length > MAX_MARKDOWN_CHARS) return sanitize(TOO_LONG_HTML)
      const tokens = markdown.parse(content, {})
      if (
        options?.stripLeadingSectionHeading === true &&
        tokens[0]?.type === 'heading_open' &&
        tokens[0].tag === 'h2' &&
        tokens[1]?.type === 'inline' &&
        tokens[2]?.type === 'heading_close'
      ) {
        tokens.splice(0, 3)
      }
      decorateTokens(tokens)
      return sanitize(markdown.renderer.render(tokens, markdown.options, {}))
    } catch {
      return ''
    }
  }
}

const browserRenderer = typeof window === 'undefined'
  ? null
  : createSafeMarkdownRenderer(window)

export function renderSafeMarkdown(content, options = {}) {
  return browserRenderer ? browserRenderer(content, options) : ''
}
