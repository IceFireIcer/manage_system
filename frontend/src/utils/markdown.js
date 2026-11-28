const ESCAPE_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (char) => ESCAPE_MAP[char])
}

function renderInline(text) {
  let out = escapeHtml(text)
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>')
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  return out
}

export function renderMarkdown(source) {
  const text = String(source ?? '')
  if (!text) return ''

  const segments = text.split(/```/)
  let html = ''
  segments.forEach((segment, index) => {
    if (index % 2 === 1) {
      const lines = segment.replace(/^[a-zA-Z0-9_-]*\n/, '')
      html += `<pre class="assistant-md__pre"><code>${escapeHtml(lines)}</code></pre>`
      return
    }
    html += renderBlock(segment)
  })
  return html
}

function renderBlock(segment) {
  const lines = segment.split(/\r?\n/)
  const parts = []
  let listBuffer = []

  const flushList = () => {
    if (!listBuffer.length) return
    parts.push(`<ul class="assistant-md__list">${listBuffer.join('')}</ul>`)
    listBuffer = []
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (/^[-*]\s+/.test(trimmed)) {
      listBuffer.push(`<li>${renderInline(trimmed.replace(/^[-*]\s+/, ''))}</li>`)
      continue
    }
    flushList()
    if (!trimmed) {
      parts.push('<br />')
      continue
    }
    parts.push(`<p class="assistant-md__p">${renderInline(trimmed)}</p>`)
  }
  flushList()
  return parts.join('')
}