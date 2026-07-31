/**
 * renderMarkdown — behaviour and, more importantly, what it refuses to do.
 *
 * Context: JARVIS's replies are markdown on the text surface as of 2026-07-31.
 * Those replies routinely quote web pages, emails and file contents, i.e. text
 * this project does not control. The renderer therefore returns React elements
 * and never HTML — these tests pin that, so a future "just use marked, it's
 * simpler" refactor has to consciously break an assertion that says why not.
 */
import { describe, it, expect } from 'vitest'
import { renderMarkdown } from './markdown'

/** Flatten a React element tree to the text a reader would see. */
function textOf(node) {
  if (node === null || node === undefined || node === false) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  return textOf(node.props?.children)
}

/** Every element type present in the tree. */
function typesOf(node, acc = []) {
  if (!node || typeof node !== 'object') return acc
  if (Array.isArray(node)) { node.forEach(n => typesOf(n, acc)); return acc }
  if (node.type) acc.push(node.type)
  return typesOf(node.props?.children, acc)
}

describe('renderMarkdown', () => {
  it('returns null for empty input', () => {
    expect(renderMarkdown('')).toBeNull()
    expect(renderMarkdown(null)).toBeNull()
  })

  it('renders plain prose as a paragraph', () => {
    const out = renderMarkdown('Bugün 31 Temmuz 2026.')
    expect(typesOf(out)).toContain('p')
    expect(textOf(out)).toBe('Bugün 31 Temmuz 2026.')
  })

  it('renders bold, italic and inline code', () => {
    const out = renderMarkdown('Net **-3.924,68 TRY**, *tahmini*, `finance(export)`')
    const types = typesOf(out)
    expect(types).toContain('strong')
    expect(types).toContain('em')
    expect(types).toContain('code')
    expect(textOf(out)).toBe('Net -3.924,68 TRY, tahmini, finance(export)')
  })

  it('renders a table with header and rows', () => {
    const out = renderMarkdown([
      '| Ay | Gelir |',
      '|----|-------|',
      '| Tem | 24.659,00 |',
      '| Ağu | 12.000,00 |',
    ].join('\n'))
    const types = typesOf(out)
    expect(types).toContain('table')
    expect(types).toContain('th')
    expect(types.filter(t => t === 'tr')).toHaveLength(3)   // header + 2 rows
    expect(textOf(out)).toContain('24.659,00')
  })

  it('renders bullet and numbered lists', () => {
    expect(typesOf(renderMarkdown('- bir\n- iki'))).toContain('ul')
    expect(typesOf(renderMarkdown('1. bir\n2. iki'))).toContain('ol')
    expect(textOf(renderMarkdown('- bir\n- iki'))).toBe('biriki')
  })

  it('renders fenced code verbatim, without parsing inline syntax inside it', () => {
    const out = renderMarkdown('```\nnot **bold** here\n```')
    expect(typesOf(out)).toContain('pre')
    expect(typesOf(out)).not.toContain('strong')
    expect(textOf(out)).toBe('not **bold** here')
  })

  it('renders headings', () => {
    expect(typesOf(renderMarkdown('## Özet'))).toContain('h4')
    expect(textOf(renderMarkdown('## Özet'))).toBe('Özet')
  })

  // ── Safety ────────────────────────────────────────────────────────────────

  it('never emits raw HTML for HTML in the source', () => {
    const out = renderMarkdown('<script>alert(1)</script> ve <b>kalın</b>')
    // The tags survive as literal TEXT, which is exactly right: they are shown,
    // not executed. dangerouslySetInnerHTML would have made both live.
    expect(textOf(out)).toContain('<script>alert(1)</script>')
    expect(typesOf(out)).not.toContain('script')
    expect(typesOf(out)).not.toContain('b')
  })

  it('does not linkify javascript: or data: URLs', () => {
    for (const href of ['javascript:alert(1)', 'data:text/html,<script>1</script>', 'file:///etc/passwd']) {
      const out = renderMarkdown(`[tıkla](${href})`)
      expect(typesOf(out)).not.toContain('a')
      expect(textOf(out)).toBe('tıkla')     // label kept, link dropped
    }
  })

  it('keeps a URL containing parentheses intact', () => {
    // Regression: the URL pattern was [^)]+, which stopped at the first inner
    // ")" and left a stray ")" in the rendered text. Found via a javascript:
    // payload, but the case that matters is an ordinary Wikipedia-style link.
    const out = renderMarkdown('[Foo](https://en.wikipedia.org/wiki/Foo_(bar))')
    expect(textOf(out)).toBe('Foo')
    expect(typesOf(out)).toContain('a')
  })

  it('linkifies http(s) URLs and opens them without window.opener', () => {
    const out = renderMarkdown('[kaynak](https://example.com/x)')
    expect(typesOf(out)).toContain('a')
    const findA = (n) => Array.isArray(n) ? n.map(findA).find(Boolean)
      : (n && typeof n === 'object'
          ? (n.type === 'a' ? n : findA(n.props?.children))
          : null)
    const a = findA(out)
    expect(a.props.href).toBe('https://example.com/x')
    expect(a.props.rel).toContain('noopener')
  })

  it('shows an image as its filename, not a broken <img>', () => {
    // JARVIS reports absolute local paths; a page served over http:// cannot
    // load file:// so an <img> would render an empty box where a readable
    // filename belongs.
    const out = renderMarkdown('![grafik](C:\\Users\\x\\data\\runs\\exec-1\\chart.png)')
    expect(typesOf(out)).not.toContain('img')
    expect(textOf(out)).toBe('chart.png')
  })

  it('leaves unsupported syntax readable instead of dropping it', () => {
    const out = renderMarkdown('> alıntı satırı')
    expect(textOf(out)).toContain('alıntı satırı')
  })

  it('handles an unterminated code fence without losing the content', () => {
    const out = renderMarkdown('```\nyarim kalmis')
    expect(textOf(out)).toContain('yarim kalmis')
  })
})
