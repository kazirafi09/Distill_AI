import { apiRequest } from '../../services/api'

export type IdentityStatus = {
  status: 'matched' | 'not_identified' | 'unable_to_verify' | 'not_performed'
  consent: 'pending' | 'accepted' | 'declined' | 'withdrawn' | 'expired'
  has_reference: boolean
  reason: string | null
  checked_at: string | null
  expires_at: string | null
  reviewed_at: string | null
  review_note: string | null
  similarity?: number | null
  threshold?: number | null
  admission_ready?: boolean
  attempts?: number
  attempt_limit?: number
  attempt_limit_reached?: boolean
  manual_review_required?: boolean
}

export type CandidateIdentity = IdentityStatus & {
  available: boolean; consent_text: string; consent_version: string; retention_days: number
}

export const identityLabels: Record<IdentityStatus['status'], string> = {
  matched: 'Matches submitted photo', not_identified: 'Not identified',
  unable_to_verify: 'Unable to verify', not_performed: 'Verification not performed',
}

export function identityLabel(data: IdentityStatus) {
  if (data.manual_review_required) return 'Manual review required'
  return data.reason === 'threshold_unconfigured' ? 'Comparison complete · recruiter review required' : identityLabels[data.status]
}

export const identityReasons: Record<string, string> = {
  reference_missing: 'No reference photo is linked. Ask your recruiter for manual review.',
  reference_unusable: 'The submitted photo could not be used. Ask your recruiter for manual review.',
  image_size: 'Use a camera frame under 5 MB.', image_format: 'The image could not be read.',
  no_face: 'Position your face in the camera and try again.', multiple_faces: 'More than one face was detected. Please be alone in the camera frame so the photo contains only you, then try again.',
  face_too_small: 'Move closer to the camera and try again.', poor_quality: 'Use clear lighting, hold still, and try again.',
  service_unavailable: 'Verification is temporarily unavailable. Manual review is available.',
  threshold_unconfigured: 'The comparison is complete. A matching threshold has not been configured; the recruiter will review the similarity score.',
}

export function loadCandidateIdentity(token: string, signal?: AbortSignal) {
  return apiRequest<CandidateIdentity>('/interviews/candidate/identity', { method: 'POST', body: { token }, signal, cache: 'no-store' })
}

export function saveIdentityConsent(token: string, choice: 'accepted' | 'declined' | 'withdrawn', version: string) {
  return apiRequest<IdentityStatus>('/interviews/candidate/identity/consent', { method: 'POST', body: { token, choice, version }, cache: 'no-store' })
}

export function verifyIdentity(token: string, frame: string) {
  return apiRequest<IdentityStatus>('/interviews/candidate/identity/verify', { method: 'POST', body: { token, frame }, cache: 'no-store' })
}

export function checkIdentityFrame(token: string, frame: string, signal: AbortSignal) {
  return apiRequest<{ face_count: number | null; reason: string | null }>('/interviews/candidate/identity/check-frame', {
    method: 'POST', body: { token, frame }, signal, cache: 'no-store',
  })
}

export function identityPath(jobId: string, applicationId: string) {
  return `/interviews/jobs/${encodeURIComponent(jobId)}/applications/${encodeURIComponent(applicationId)}/identity`
}
