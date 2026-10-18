import { apiRequest } from '../../services/api'

export const dimensions = ['relevance', 'accuracy', 'completeness', 'articulation'] as const
export type Dimension = typeof dimensions[number]
export type Run = {
  id: string; job_id: string; application_id: string | null; kind: 'f5' | 'f6'; synthetic: boolean
  label: string; status: string; stage: string; attempt: number; max_attempts: number
  completed_groups: number; total_groups: number; error_code: string | null; can_retry: boolean
  profile_version: number; prompt_version: string; rubric_version: string; created_at: string
  updated_at: string; heartbeat_at: string | null; next_attempt_at: string
  parent_run_id: string | null
  can_recover?: boolean; recovered_from?: string | null
}
export type Candidate = { id: string; name: string; transcript_status: string | null; blocker: string | null; ready: boolean }
export type Workspace = { candidates: Candidate[]; synthetic_demo_available: boolean; has_more_candidates: boolean; offset: number; runs: Run[]; has_more_runs: boolean; profile_ready: boolean }
export type Turn = { id: string; speaker_role: 'candidate' | 'recruiter'; text: string; session_start_ms: number; session_end_ms: number }
export type Evidence = { turn_id: string; quote: string; session_start_ms: number; session_end_ms: number }
export type Evaluation = {
  score: number | null; score_100: number | null; reason: string; justification: string; evidence: Evidence[]
  accuracy_warning: string | null; reference_evidence: { reference_id: string; quote: string; url: string; version: string }[]
}
export type Answer = { group: {
  id: string; topic: string; question_turn_ids: string[]; answer_turn_ids: string[]; follow_up_turn_ids: string[]
  clarification_turn_ids: string[]; revisit_turn_ids: string[]; answer_state: string; criterion_ids: string[]
}; evaluations: Record<Dimension, Evaluation> }
export type Summary = { score_100: string | null; assessed_count: number; total_groups: number; not_assessable_count: number }
export type Finding = { criterion_id: string; text: string; source_ids: string[] }
export type F6Source = { id: string; kind: 'cv' | 'interview' | 'coverage'; criterion_ids: string[];
  decision?: string; justification?: string; dimension?: string; score_100?: number | null; exploration?: string
  evidence?: { quote: string; turn_id?: string; session_start_ms?: number; parse_id?: string; file_version_id?: string; source_id?: string; start?: number; end?: number }[] }
export type F6Report = { findings: Record<'strengths' | 'weaknesses' | 'evidence_gaps' | 'recommended_areas', Finding[]> & {
  suitability: { position: string; rationale: string; source_ids: string[] } }
  component_scores: { cv: { aggregate: string; dimensions: {dimension: string; score: string; approved_percentage: string}[]; note: string }; interview: Record<Dimension, Summary> }
  source_registry: F6Source[]; provenance: { f5_run_id: string; cv_assessment_id: string; repairs_used: number }
}
export type Result = {
  run: Run; result: null | { publishable: boolean; f6_ready: boolean; warnings: string[];
    coverage?: { criterion_id: string; group_ids: string[]; exploration: string; performance_score: number | null }[];
    findings?: F6Report['findings']; component_scores?: F6Report['component_scores']; source_registry?: F6Source[]; provenance?: F6Report['provenance'] }
  answers: Answer[]; dimension_summaries: Record<Dimension, Summary>; criteria: { id: string; text: string }[]
  source_turns: Turn[]; outdated: boolean; f6: null | { ready: boolean; blocker: string | null; review_required: boolean }
}

export const loadWorkspace = (token: string, jobId: string, offset: number, signal?: AbortSignal) =>
  apiRequest<Workspace>(`/assessments/jobs/${jobId}?offset=${offset}`, { token, signal, cache: 'no-store' })
export const loadResult = (token: string, id: string, signal?: AbortSignal) =>
  apiRequest<Result>(`/assessments/runs/${id}/result`, { token, signal, cache: 'no-store' })
export const startF5 = (token: string, jobId: string, applicationId?: string) =>
  apiRequest<Run>(`/assessments/jobs/${jobId}/f5`, { token, method: 'POST', body: applicationId
    ? { source: 'interview', application_id: applicationId } : { source: 'synthetic_demo' } })
export const retryF5 = (token: string, id: string) =>
  apiRequest<Run>(`/assessments/runs/${id}/retry`, { token, method: 'POST' })
export const recoverF5 = (token: string, id: string) =>
  apiRequest<Run>(`/assessments/runs/${id}/recover`, { token, method: 'POST' })
export const startF6 = (token: string, id: string, confirmReview: boolean) =>
  apiRequest<Run>(`/assessments/runs/${id}/f6`, { token, method: 'POST', body: { confirm_f5_review: confirmReview } })

export type FullReport = { title: string; filename: string; metadata: { run: Run; candidate: { name: string } };
  sections: { title: string; blocks: { kind: 'text' | 'heading' | 'quote' | 'note' | 'warning'; text: string }[] }[] }
export const loadFullReport = (token: string, id: string, signal?: AbortSignal) =>
  apiRequest<FullReport>(`/assessments/runs/${id}/report`, { token, signal, cache: 'no-store' })
export const downloadFullReport = (token: string, id: string, signal?: AbortSignal) =>
  apiRequest<Blob>(`/assessments/runs/${id}/report.pdf`, { token, signal, cache: 'no-store', responseType: 'blob' })
