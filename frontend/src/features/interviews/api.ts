import { apiRequest } from '../../services/api'
import type { Assessment, Results, Score } from '../cv/CVResults'

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
  profile?: Results['profile']
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

export type InvitationMode = 'new' | 'retry_failed' | 'reissue'
export type InvitationPreview = {
  batch_id: string
  preview_key: string
  expires_at: string
  job_title: string
  sender: string
  subject: string
  body: string
  link_days: number
  send_count: number
  mode: InvitationMode
  recipients: { application_id: string; name: string; email: string; action: string; reason: string }[]
}

export type InvitationStatus = {
  id: string
  application_id: string
  name: string
  email: string
  status: string
  attempts: number
  error_code: string | null
  link_status: string
  expires_at: string
}

export function prepareInvitations(token: string, jobId: string, mode: InvitationMode) {
  return apiRequest<InvitationPreview>(`/interviews/jobs/${encodeURIComponent(jobId)}/invitations/preview`, {
    method: 'POST', token, body: { mode },
  })
}

export function confirmInvitations(token: string, jobId: string, preview: InvitationPreview) {
  return apiRequest<{ queued?: number; already_confirmed: boolean; message: string }>(
    `/interviews/jobs/${encodeURIComponent(jobId)}/invitations/confirm`, {
      method: 'POST', token,
      body: { batch_id: preview.batch_id, preview_key: preview.preview_key, confirmed: true },
    })
}

export function loadInvitations(token: string, jobId: string, signal?: AbortSignal) {
  return apiRequest<InvitationStatus[]>(`/interviews/jobs/${encodeURIComponent(jobId)}/invitations`, { token, signal })
}

export function revokeInvitation(token: string, jobId: string, invitationId: string) {
  return apiRequest<{ status: string }>(
    `/interviews/jobs/${encodeURIComponent(jobId)}/invitations/${encodeURIComponent(invitationId)}/revoke`,
    { method: 'POST', token })
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
