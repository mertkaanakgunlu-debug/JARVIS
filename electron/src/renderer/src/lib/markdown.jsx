/**
 * markdown — a small, dependency-free renderer for JARVIS replies.
 *
 * Why it exists: the system prompt banned markdown on every surface until
 * 2026-07-31, because the voice-mode rule ("no bold, no headers, no bullet
 * lists") was loaded unconditionally. Lifting that ban for the text surface
 * immediately made this necessary — the Transcript panel rendered `{t.text}`
 * verbatim, so a reply containing a table or a bolded figure would have shown
 * the owner raw `**` and `|` characters. Half of that change shipped alone is
 * worse than neither half.
 *
 * Two deliberate constraints:
 *
 *   1. No dependency. `marked`/`react-markdown` pull a parser (and, for HTML
 *      output, a sanitizer) into a renderer that needs to format a chat reply.
 *      The subset below is what an assistant actually emits.
 *
 *   2. NEVER dangerouslySetInnerHTML. Everything returns React elements, so
 *      model output — which can quote arbitrary web pages, emails and file
 *      contents — cannot become live markup. This is the whole reason not to
 *      take the shortcut of converting to an HTML string.
 *
 * Unsupported syntax degrades to plain text rather than disappearing: an
 * unrecognised construct is still readable, which is the right failure mode for
 * a transcript.
 */
import React from 'react'

// ── Inline: `code`, **bold**, *italic*, [text](url), ![alt](src) ─────────────
//
// Images render as their filename in code style rather than an <img>: JARVIS
// reports absolute local paths (data/runs/exec-…/chart.png), which a page served
// over http:// cannot load from file://, so an <img> would be a broken-image
// box where a readable path belongs.
// The URL part allows ONE level of balanced parentheses. `[^)]+` looked
// sufficient until a test fed it `javascript:alert(1)`: the match stopped at the
// inner ")" and left a stray ")" in the output. The security check still held —
// no <a> was produced — but the same flaw breaks perfectly ordinary links such
// as en.wikipedia.org/wiki/Foo_(bar), which is the case that actually matters.
const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(!?\[[^\]]*\]\((?:[^()]|\([^()]*\))*\))/g

function renderInline(text, keyPrefix = 'i') {
  const out = []
  let last = 0
  let m
  let n = 0
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const key = `${keyPrefix}-${n++}`
    if (tok.startsWith('`')) {
      out.push(<code key={key} className="md-code">{tok.slice(1, -1)}</code>)
    } else if (tok.startsWith('**')) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('![')) {
      const src = tok.slice(tok.indexOf('](') + 2, -1)
      const name = src.split(/[\\/]/).pop() || src
      out.push(<code key={key} className="md-code" title={src}>{name}</code>)
    } else if (tok.startsWith('[')) {
      const label = tok.slice(1, tok.indexOf(']('))
      const href = tok.slice(tok.indexOf('](') + 2, -1)
      // Only http(s) becomes a link. A javascript:/data: URL in model output
      // must never be clickable.
      const safe = /^https?:\/\//i.test(href)
      out.push(safe
        ? <a key={key} href={href} target="_blank" rel="noreferrer noopener">{label}</a>
        : <span key={key}>{label}</span>)
    } else {
      out.push(<em key={key}>{tok.slice(1, -1)}</em>)
    }
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const isTableRow = (l) => l.trim().startsWith('|') && l.trim().endsWith('|')
const isTableSep = (l) => /^\s*\|[\s:|-]+\|\s*$/.test(l) && l.includes('-')
const splitRow = (l) => l.trim().slice(1, -1).split('|').map(c => c.trim())

/** Render markdown text as React elements. Never produces raw HTML. */
export function renderMarkdown(text) {
  if (!text) return null
  const lines = String(text).split('\n')
  const blocks = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code — consumed verbatim, no inline parsing inside.
    if (line.trim().startsWith('```')) {
      const body = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) body.push(lines[i++])
      i++ // closing fence (or EOF — an unterminated block still renders)
      blocks.push(<pre key={`b${blocks.length}`} className="md-pre"><code>{body.join('\n')}</code></pre>)
      continue
    }

    // Table — header, separator, then rows.
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line)
      i += 2
      const rows = []
      while (i < lines.length && isTableRow(lines[i])) rows.push(splitRow(lines[i++]))
      blocks.push(
        <table key={`b${blocks.length}`} className="md-table">
          <thead><tr>{head.map((c, x) => <th key={x}>{renderInline(c, `th${x}`)}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, y) => (
              <tr key={y}>{r.map((c, x) => <td key={x}>{renderInline(c, `td${y}-${x}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }

    // Heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line)
    if (h) {
      const Tag = `h${Math.min(6, h[1].length + 2)}`
      blocks.push(<Tag key={`b${blocks.length}`} className="md-h">{renderInline(h[2], `h${i}`)}</Tag>)
      i++
      continue
    }

    // List — bullets and numbers, kept in one block so spacing stays tight.
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items = []
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))
        i++
      }
      const Tag = ordered ? 'ol' : 'ul'
      blocks.push(
        <Tag key={`b${blocks.length}`} className="md-list">
          {items.map((it, x) => <li key={x}>{renderInline(it, `li${x}`)}</li>)}
        </Tag>,
      )
      continue
    }

    // Blank line — paragraph break.
    if (!line.trim()) { i++; continue }

    // Paragraph: consecutive non-blank lines that start no other block.
    const para = []
    while (
      i < lines.length && lines[i].trim() &&
      !lines[i].trim().startsWith('```') &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) &&
      !isTableRow(lines[i])
    ) para.push(lines[i++])
    blocks.push(
      <p key={`b${blocks.length}`} className="md-p">{renderInline(para.join(' '), `p${blocks.length}`)}</p>,
    )
  }

  return blocks
}

export default renderMarkdown
