import type { F6Report, F6Source, Result, Run } from './api.ts'

export const findingSections = ['strengths', 'weaknesses', 'evidence_gaps'] as const
export type FindingSection = typeof findingSections[number]
export const findingLabels: Record<FindingSection, string> = { strengths: 'Strength', weaknesses: 'Weakness', evidence_gaps: 'Gap' }

export function savedReport(data: Result): F6Report | null {
  const result = data.result
  return result?.findings && result.component_scores && result.source_registry && result.provenance
    ? result as F6Report : null
}

export function findingsFor(report: F6Report | null) {
  return findingSections.flatMap(section => (report?.findings[section] ?? []).map((finding, index) => ({
    ...finding, section, key: `${section}-${index}`,
  })))
}

export function candidateHistory(run: Run, runs: Run[]) {
  const history = runs.filter(item => run.application_id
    ? item.application_id === run.application_id
    : item.synthetic === run.synthetic && item.label === run.label && item.application_id === null)
  return [run, ...history.filter(item => item.id !== run.id)].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

export function cvCoverage(sources: F6Source[], criterionId: string) {
  const decisions = new Set(sources.filter(source => source.kind === 'cv' && source.criterion_ids.includes(criterionId)).map(source => source.decision))
  if (decisions.has('unmet')) return { label: 'Weakness', tone: 'weaknesses' }
  if (decisions.has('insufficient_evidence')) return { label: 'Gap', tone: 'evidence_gaps' }
  if (decisions.has('partially_met')) return { label: 'Partial support', tone: 'evidence_gaps' }
  if (decisions.has('met')) return { label: 'Strength', tone: 'strengths' }
  return { label: 'Not available', tone: 'unknown' }
}

export function numericScore(value: number | string | null | undefined) {
  if (value === null || value === undefined || (typeof value === 'string' && !value.trim())) return null
  const score = Number(value)
  return Number.isFinite(score) && score >= 0 && score <= 100 ? score : null
}

export function formatScoreValue(value: number | string | null | undefined) {
  const score = numericScore(value)
  return score === null ? '—' : score.toFixed(score % 1 ? 2 : 0)
}
