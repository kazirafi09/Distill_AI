import { apiRequest } from '../../services/api'
import type { Assessment, Score } from '../cv/CVResults'

export type ShortlistFilters = {
  mode: 'top_k' | 'minimum_score' | 'both'
  k: number | null
  minimum_score: string | null
}

type ScreeningRun = {
  id: string
  status: string
}

export type PreviewCandidate = Assessment

export type ScreeningResults = {
  run: ScreeningRun | null
  ranked: Assessment[]
  incomplete: Assessment[]
  warnings: string[]
}

export type ExcludedCandidate = PreviewCandidate & {
  exclusion_reason:
    | 'outside_top_k'
    | 'below_minimum_score'
    | 'outdated_assessment'
    | 'assessment_not_completed'
    | 'current_ranking_unavailable'
    | 'assessment_missing'
    | 'score_missing'
    | 'score_invalid'
}

export type ShortlistPreview = {
  job_id: string
  run: ScreeningRun | null
  filters: ShortlistFilters
  preview_key: string
  proposed: PreviewCandidate[]
  proposed_count: number
  excluded: ExcludedCandidate[]
  warnings: string[]
}

export type SavedInterviewee = {
  application_id: string
  assessment_id: string
  name: string
  email: string
  score: Score
  rank: number
  manually_added: boolean
  outdated: boolean
}

export type SavedShortlist = {
  id: string
  job_id: string
  screening_run_id: string
  filters: ShortlistFilters
  proposed_application_ids: string[]
  members: SavedInterviewee[]
  selected_count: number
  outdated: boolean
  warnings: string[]
}

export function loadScreeningResults(
  token: string,
  jobId: string,
  signal?: AbortSignal,
) {
  return apiRequest<ScreeningResults>(
    `/cv/screening/jobs/${encodeURIComponent(jobId)}/results`,
    { token, signal },
  )
}

export function previewShortlist(
  token: string,
  jobId: string,
  filters: ShortlistFilters,
  signal?: AbortSignal,
) {
  return apiRequest<ShortlistPreview>(
    `/interviews/jobs/${encodeURIComponent(jobId)}/shortlist/preview`,
    { method: 'POST', token, body: filters, signal },
  )
}

export function loadShortlist(
  token: string,
  jobId: string,
  signal?: AbortSignal,
) {
  return apiRequest<SavedShortlist | null>(
    `/interviews/jobs/${encodeURIComponent(jobId)}/shortlist`,
    { token, signal },
  )
}

export function saveShortlist(
  token: string,
  preview: ShortlistPreview,
  selectedApplicationIds: string[],
) {
  return apiRequest<SavedShortlist>(
    `/interviews/jobs/${encodeURIComponent(preview.job_id)}/shortlist`,
    {
      method: 'PUT',
      token,
      body: {
        filters: preview.filters,
        preview_key: preview.preview_key,
        selected_application_ids: selectedApplicationIds,
      },
    },
  )
}
