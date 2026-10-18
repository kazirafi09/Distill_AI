import { apiRequest } from '../../services/api'

export type TranscriptSnapshot = {
  transcript: { id: string; version: number; status: string; quality_flags: string[]; error_code: string | null } | null
  capture: { status: 'inactive' | 'connecting' | 'waiting_for_microphone' | 'listening' | 'stopping'; roles: ('recruiter' | 'candidate')[] }
  turns: {
    id: string; stream_id: string; speaker_role: 'recruiter' | 'candidate'; text: string
    stream_start_ms: number; stream_end_ms: number; created_at: string
    speech_final: boolean | null
  }[]
  has_earlier_turns: boolean
}

export function loadTranscript(token: string, jobId: string, applicationId: string, signal?: AbortSignal) {
  return apiRequest<TranscriptSnapshot>(
    `/interviews/jobs/${encodeURIComponent(jobId)}/applications/${encodeURIComponent(applicationId)}/transcript`,
    { token, signal, cache: 'no-store' },
  )
}

export type InterviewSessionStatus =
  | 'ready'
  | 'scheduled'
  | 'in_progress'
  | 'closing'
  | 'completed'
  | 'cancelled'

export type InterviewSession = {
  id: string
  application_id: string
  status: InterviewSessionStatus
  started_at: string | null
  completed_at: string | null
}

export type RoomCredentials = {
  server_url: string
  participant_token: string
}

export type RecruiterInterviewStatus =
  | (InterviewSession & { candidate_admitted: boolean })
  | { status: 'not_started'; candidate_admitted: false }

export type CandidateInterviewStatus = {
  status: InterviewSessionStatus | 'waiting_for_recruiter'
  can_join: boolean
  waiting_for_admission?: boolean
}

function sessionPath(jobId: string, applicationId: string) {
  return `/interviews/jobs/${encodeURIComponent(jobId)}/applications/${encodeURIComponent(applicationId)}/session`
}

export function startInterview(token: string, jobId: string, applicationId: string) {
  return apiRequest<InterviewSession & RoomCredentials>(
    `${sessionPath(jobId, applicationId)}/start`,
    { method: 'POST', token, cache: 'no-store' },
  )
}

export function loadInterviewStatus(
  token: string,
  jobId: string,
  applicationId: string,
  signal?: AbortSignal,
) {
  return apiRequest<RecruiterInterviewStatus>(
    sessionPath(jobId, applicationId),
    { token, signal, cache: 'no-store' },
  )
}

export function admitCandidate(token: string, jobId: string, applicationId: string) {
  return apiRequest<InterviewSession & { candidate_admitted: boolean }>(
    `${sessionPath(jobId, applicationId)}/admit`,
    { method: 'POST', token, cache: 'no-store' },
  )
}

export function joinRecruiterInterview(token: string, jobId: string, applicationId: string) {
  return apiRequest<RoomCredentials>(
    `${sessionPath(jobId, applicationId)}/join`,
    { method: 'POST', token, cache: 'no-store' },
  )
}

export function endInterview(token: string, jobId: string, applicationId: string) {
  return apiRequest<InterviewSession>(
    `${sessionPath(jobId, applicationId)}/end`,
    { method: 'POST', token, cache: 'no-store' },
  )
}

export function loadCandidateInterviewStatus(invitationToken: string, signal?: AbortSignal) {
  return apiRequest<CandidateInterviewStatus>('/interviews/candidate/status', {
    method: 'POST', body: { token: invitationToken }, signal, cache: 'no-store',
  })
}

export function joinCandidateInterview(invitationToken: string) {
  return apiRequest<RoomCredentials>('/interviews/candidate/join', {
    method: 'POST', body: { token: invitationToken }, cache: 'no-store',
  })
}
