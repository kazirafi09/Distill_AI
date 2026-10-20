export function profileRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function profileText(value: unknown): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  const claim = profileRecord(value)
  return typeof claim.value === 'string' ? claim.value.trim() : ''
}

export function profileList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

export function profileItems(value: unknown): string[] {
  return [...new Set(profileList(value).map(profileText).filter(Boolean))]
}

export function profileLink(value: unknown): string | null {
  const text = profileText(value)
  try {
    const url = new URL(text)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}

export function candidateFileLabel(candidateName: string, filename: string | null, index = 0, total = 1): string {
  const extension = filename?.match(/\.(pdf|docx)$/i)?.[0].toLowerCase() ?? ''
  return `${candidateName.trim() || 'Candidate'} — CV${total > 1 ? ` ${index + 1}` : ''}${extension}`
}
