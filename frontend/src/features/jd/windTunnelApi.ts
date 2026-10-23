import { apiRequest } from '../../services/api'
import type { EditableProfile, JDConversation } from './api'

export type WindTunnelSuggestion = {
  id: string; title: string; reason: string; affected_areas: string[]
  status: 'ready' | 'applied' | 'stale'; applied_at: string | null
  effect_scope: string; finding_ids: string[]
  patches: { kind: string; field?: string; criterion_id?: string; expected: unknown; proposed: unknown }[]
}
export type WindTunnelBatch = {
  id: string; run_id: string | null; status: 'queued' | 'running' | 'completed' | 'limited' | 'failed' | 'cancelled' | 'stale'
  stale: boolean; trigger: string; created_at: string; error_code: string | null; stage: string | null
  coverage: { completed_assessments: number; expected_assessments: number; complete_cohorts: number } | null
  suggestions: WindTunnelSuggestion[]
}
export type WindTunnelHistory = { edit_version: string; editing_locked: boolean; batches: WindTunnelBatch[] }
export type WindTunnelPreview = { edit: { content: string; screening_profile: EditableProfile }; review_token: string }
const path = (job: string) => `/jd/descriptions/${job}/suggestions`
export const getWindTunnel = (token: string, job: string, signal: AbortSignal) => apiRequest<WindTunnelHistory>(path(job), { token, signal })
export const moreWindTunnel = (token: string, job: string, requestId: string, signal: AbortSignal) => apiRequest(`${path(job)}/more`, { token, method: 'POST', body: { request_id: requestId }, signal })
export const actWindTunnel = (token: string, job: string, batch: string, action: 'retry' | 'cancel', signal: AbortSignal) => apiRequest(`${path(job)}/batches/${batch}/${action}`, { token, method: 'POST', signal })
export const previewWindTunnel = (token: string, job: string, id: string, version: string, signal: AbortSignal) => apiRequest<WindTunnelPreview>(`${path(job)}/preview`, { token, method: 'POST', body: { suggestion_ids: [id], edit_version: version }, signal })
export const applyWindTunnel = (token: string, job: string, id: string, version: string, reviewToken: string, signal: AbortSignal) => apiRequest<JDConversation>(`${path(job)}/apply`, { token, method: 'POST', body: { suggestion_ids: [id], edit_version: version, review_token: reviewToken, criteria_reviewed: true }, signal })
