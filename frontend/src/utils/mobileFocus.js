export function keepFocusedFieldVisible(event) {
  if (window.innerWidth >= 768) return
  const target = event?.target
  if (!(target instanceof HTMLElement)) return
  if (!target.matches('input, textarea, [contenteditable="true"]')) return
  window.setTimeout(() => {
    if (!target.isConnected) return
    target.scrollIntoView({ block: 'center', inline: 'nearest' })
  }, 260)
}