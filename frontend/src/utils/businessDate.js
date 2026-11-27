const SHANGHAI_TIME_ZONE = 'Asia/Shanghai'

export const REVERSE_RANGE_MESSAGE = '开始日期不能晚于结束日期'

function pad(value) {
  return String(value).padStart(2, '0')
}

function shanghaiParts(date) {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const values = {}
  for (const part of parts) {
    if (part.type !== 'literal') values[part.type] = part.value
  }
  return values
}

export function businessDate(offsetDays = 0) {
  const parts = shanghaiParts(new Date())
  const base = new Date(
    Date.UTC(
      Number(parts.year),
      Number(parts.month) - 1,
      Number(parts.day) + offsetDays,
    ),
  )
  return `${base.getUTCFullYear()}-${pad(base.getUTCMonth() + 1)}-${pad(base.getUTCDate())}`
}

export function businessToday() {
  return businessDate(0)
}

export function businessNowInput() {
  const parts = shanghaiParts(new Date())
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:00`
}

export function validateDateRange(range) {
  if (!Array.isArray(range) || range.length !== 2) return ''
  const [start, end] = range
  if (start && end && start > end) return REVERSE_RANGE_MESSAGE
  return ''
}