import type { Candidate, Result, SavedPerformance } from './api.ts'
import { savedReport } from './assessmentView.ts'

export type ComparisonSelection = { id: string; name: string; runId: string; synthetic: boolean }
export type ComparisonEntry = { data: Result; performance: SavedPerformance | null; performanceWarning?: string }

export function comparisonSelection(candidate: Candidate): ComparisonSelection | null {
  const run = candidate.assessment
  if (run?.kind !== 'f6' || run.status !== 'completed') return null
  return { id: candidate.id, name: candidate.name, runId: run.id, synthetic: !!(candidate.synthetic || run.synthetic) }
}

export function toggleComparison(selected: ComparisonSelection[], next: ComparisonSelection) {
  if (selected.some(item => item.id === next.id)) return selected.filter(item => item.id !== next.id)
  return selected.length < 2 ? [...selected, next] : selected
}

export function validateComparison(data: Result, selection: ComparisonSelection, jobId: string) {
  if (data.run.id !== selection.runId || data.run.job_id !== jobId || data.run.kind !== 'f6'
      || data.run.status !== 'completed' || !savedReport(data)
      || data.run.synthetic !== selection.synthetic
      || (data.run.application_id !== selection.id && !(data.run.synthetic && data.run.application_id === null))) {
    throw new Error('The selected completed assessment is no longer available for this candidate and job. Refresh the list and select again.')
  }
}

// Same IDs with changed text are separate rows; never align different requirements
// by position or by an AI guess about their meaning.
export function comparisonCriteria(entries: ComparisonEntry[]) {
  const rows = new Map<string, {key: string; id: string; text: string}>()
  for (const {data} of entries) for (const criterion of data.criteria) {
    const key = JSON.stringify([criterion.id, criterion.text])
    rows.set(key, {key, ...criterion})
  }
  return [...rows.values()]
}

export function comparisonWarnings(entries: ComparisonEntry[]) {
  const warnings = new Set<string>()
  const signatures = entries.map(({data}) => JSON.stringify([...data.criteria].sort((a, b) => a.id.localeCompare(b.id)).map(item => [item.id, item.text])))
  if (new Set(signatures).size > 1 || new Set(entries.map(({data}) => data.run.profile_version)).size > 1) {
    warnings.add('Criteria versions differ. Changed or missing requirements are shown separately; these assessments do not provide an equivalent comparison.')
  }
  if (new Set(entries.map(({data}) => `${data.run.rubric_version}/${data.run.prompt_version}`)).size > 1) {
    warnings.add('Assessment rubric or analysis versions differ. Review the saved methods before comparing conclusions.')
  }
  const cvPolicies = entries.map(({data}) => JSON.stringify(savedReport(data)?.component_scores.cv.dimensions
    .map(item => [item.dimension, item.approved_percentage]).sort()))
  if (new Set(cvPolicies).size > 1) warnings.add('CV scoring weights differ. CV totals are not directly comparable.')
  const policies = entries.map(({performance}) => performance?.scoring_settings)
  if (policies.some(policy => !policy)) warnings.add('Interview scoring compatibility could not be established for every candidate. Missing performance results are not zero.')
  else if (new Set(policies.map(policy => `${policy!.revision}/${policy!.policy_sha256}/${policy!.rubric_version ?? ''}`)).size > 1) {
    warnings.add('Interview scoring revisions or policies differ. Interview and combined totals are not directly comparable.')
  }
  if (new Set(entries.map(({data}) => JSON.stringify(data.final_score?.policy ?? null))).size > 1) {
    warnings.add('Combined-score policies differ or are unavailable. Review component scores separately.')
  }
  for (const {data, performance, performanceWarning} of entries) {
    if (data.outdated) warnings.add(`${data.run.label}: this assessment uses older job criteria.`)
    for (const warning of [...(data.result?.warnings ?? []), ...(data.final_score?.warnings ?? []), ...(performance?.warnings ?? [])]) warnings.add(`${data.run.label}: ${warning}`)
    if (performanceWarning) warnings.add(`${data.run.label}: ${performanceWarning}`)
  }
  return [...warnings]
}
