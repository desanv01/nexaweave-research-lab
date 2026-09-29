# Safe Markdown in reports and interactions

Step4Report and Step5Interaction use one `renderSafeMarkdown` function for
every dynamic HTML sink. Interview placeholders use Vue text children.
The renderer accepts strings only; other values return empty output without
coercion. Inputs over 262,144 JavaScript code units display the fixed
`[Content too long to display]` notice and are not parsed. This bound
does not establish a hard browser CPU or memory deadline.

Markdown-it parses with raw HTML, linkification, typography, plugins, and
syntax highlighting disabled. Valid headings, emphasis, code, paragraphs,
quotes, nested lists, rules, and semantic tables remain available.
Headings are shifted one level to match the inherited report display.
Report sections alone suppress an initial Markdown level-two heading;
chat and interview text keep their headings. A heading inside a code
fence remains literal code. Code and raw HTML display as text.

Images render only escaped alt text, with no image or other loading node.
Links are active only when their explicit URL has an HTTP or HTTPS scheme
and a valid host. Relative, protocol-relative, data, mail, script, file,
and other schemes remain inert. A private DOMPurify instance performs a
final pass with minimal tag and attribute allowlists. Its attribute hook
rechecks URL policy, fixed CSS classes, bounded ordered-list starts, and
bounded list indentation levels. It removes style, source URLs, media,
forms, custom elements, event handlers, arbitrary id/name, SVG, and
MathML. No replacements or untrusted concatenation happen after this
sanitization. If DOMPurify cannot run, rendering fails closed.

The inherited CSS class names are generated from parser tokens before
sanitization. Ordered lists use Markdown's actual `start` values rather
than the old regex renumbering of separate lists. Step5 chat uses native
list markers so its visual numbering respects each list's start and
nested structure.

This protects the listed report and chat HTML sinks. It does not replace
CSP, authentication, broader application input controls, or browser
isolation. Main will run DOM tests, build and browser review on the exact
revision.
