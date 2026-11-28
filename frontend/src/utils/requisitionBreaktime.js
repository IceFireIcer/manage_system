const MODE_PREFIX = 'requisition-breaktime:'
const RECEIPT_PREFIX = 'requisition-receipts:'
const MAX_RECEIPTS = 5

function modeKey(ownerId) {
  return `${MODE_PREFIX}${ownerId}`
}

function receiptKey(ownerId) {
  return `${RECEIPT_PREFIX}${ownerId}`
}

export function readBreaktimeMode(ownerId) {
  if (!ownerId) return false
  try {
    return window.sessionStorage.getItem(modeKey(ownerId)) === '1'
  } catch {
    return false
  }
}

export function writeBreaktimeMode(ownerId, enabled) {
  if (!ownerId) return
  try {
    window.sessionStorage.setItem(modeKey(ownerId), enabled ? '1' : '0')
  } catch {}

}

function sanitizeReceipt(receipt) {
  if (!receipt || typeof receipt !== 'object') return null
  const lines = Array.isArray(receipt.lines)
    ? receipt.lines
        .filter((line) => line && typeof line.name === 'string' && line.name.trim() !== '')
        .map((line) => ({
          name: line.name,
          quantity: Number(line.quantity) || 0,
          unit: typeof line.unit === 'string' ? line.unit : '',
        }))
    : []
  if (!lines.length) return null
  return {
    orderNo: typeof receipt.orderNo === 'string' ? receipt.orderNo : '',
    ownerName: typeof receipt.ownerName === 'string' ? receipt.ownerName : '',
    businessAt: typeof receipt.businessAt === 'string' ? receipt.businessAt : '',
    createdAt: Number(receipt.createdAt) || 0,
    lines,
  }
}

export function readReceipts(ownerId) {
  if (!ownerId) return []
  try {
    const raw = window.localStorage.getItem(receiptKey(ownerId))
    if (!raw) return []
    const data = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    return data.map(sanitizeReceipt).filter(Boolean).slice(0, MAX_RECEIPTS)
  } catch {
    return []
  }
}

export function appendReceipt(ownerId, receipt) {
  const incoming = sanitizeReceipt(receipt)
  if (!ownerId || !incoming) return readReceipts(ownerId)
  const key = incoming.orderNo || `local-${incoming.createdAt}`
  const existing = readReceipts(ownerId).filter(
    (item) => (item.orderNo || `local-${item.createdAt}`) !== key,
  )
  const next = [incoming, ...existing].slice(0, MAX_RECEIPTS)
  try {
    window.localStorage.setItem(receiptKey(ownerId), JSON.stringify(next))
  } catch {}

  return next
}

export function clearReceipts(ownerId) {
  if (!ownerId) return
  try {
    window.localStorage.removeItem(receiptKey(ownerId))
  } catch {}

}