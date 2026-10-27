import type { InvitationSchedule } from './api'

export type TimeInputs = Record<string, { start: string; end: string }>

export function localTimeInput(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ''
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

export function scheduleSummary(schedule?: InvitationSchedule | null) {
  if (!schedule) return 'Time not set'
  const format = new Intl.DateTimeFormat('en-GB', { timeZone: schedule.display_timezone,
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  return `${format.format(new Date(schedule.starts_at))} – ${format.format(new Date(schedule.ends_at))} · ${schedule.display_timezone}`
}

export function scheduleError(times: TimeInputs, ids: string[], now = Date.now()) {
  for (const id of ids) {
    const item = times[id]
    const start = item?.start ? new Date(item.start).getTime() : NaN
    const end = item?.end ? new Date(item.end).getTime() : NaN
    if (!Number.isFinite(start) || !Number.isFinite(end)) return 'Set a start and end time for every candidate.'
    if (end <= start) return 'Each end time must be after its start time.'
    if (end <= now) return 'This interview window has ended. Choose an end time in the future.'
  }
  return ''
}
