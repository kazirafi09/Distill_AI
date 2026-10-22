import { useEffect, useId, useState } from 'react'
import { apiRequest } from '../../services/api'
import { identityLabel, identityPath } from './identityApi'
import type { IdentityStatus } from './identityApi'
import './IdentityVerification.css'

export default function RecruiterIdentity({ token, jobId, applicationId, onReady }: {
  token: string; jobId: string; applicationId: string; onReady?: (ready: boolean) => void
}) {
  const [data, setData] = useState<IdentityStatus | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [reviewOpen, setReviewOpen] = useState(false)
  const reviewId = useId()
  const path = identityPath(jobId, applicationId)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await apiRequest<IdentityStatus>(path, { token, signal: controller.signal, cache: 'no-store' })
        if (!controller.signal.aborted) { setData(result); setError(''); onReady?.(!!result.admission_ready) }
      } catch (caught) { if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load verification.'); onReady?.(false) } }
      finally { if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 5000) }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, path, revision, onReady])

  async function save(action: 'review' | 'delete') {
    setBusy(true); setError('')
    try {
      const result = await apiRequest<IdentityStatus>(`${path}/${action}`, { method: 'POST', token, body: action === 'review' ? { note } : undefined })
      setData(result); onReady?.(!!result.admission_ready)
      setNote(''); setRevision(value => value + 1)
      if (action === 'review') setReviewOpen(false)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not update verification.') }
    finally { setBusy(false) }
  }

  const hasScore = typeof data?.similarity === 'number' && Number.isFinite(data.similarity)
  const deleted = !!data && ['withdrawn', 'expired'].includes(data.consent)
  const tone = data?.manual_review_required ? 'attention' : data?.status === 'matched' ? 'success'
    : data && (data.status === 'unable_to_verify' || data.status === 'not_identified' || data.consent === 'declined' || deleted || !data.has_reference) ? 'attention' : 'neutral'
  const issues: Record<string, [string, string, string]> = {
    poor_quality: ['Photo quality was too low', 'The camera image was too dark, bright or blurry to compare.', 'Ask the candidate to retake with better lighting, or review manually.'],
    no_face: ['No face was detected', 'The candidate’s face was not visible in the captured image.', 'Ask the candidate to face the camera and retry, or review them manually.'],
    multiple_faces: ['More than one face was detected', 'The image must contain only the candidate.', 'Ask the candidate to be alone in the camera frame and retry, or review them manually.'],
    face_too_small: ['The candidate was too far from the camera', 'Their face was too small in the image to compare.', 'Ask the candidate to move closer and retake the photo, or review them manually.'],
    reference_missing: ['Application photo is missing', 'There is no submitted photo available for comparison.', 'Review the candidate manually before deciding whether to admit them.'],
    reference_unusable: ['Application photo could not be used', 'The submitted photo could not be read or did not contain one clear face.', 'Review the candidate manually before deciding whether to admit them.'],
    service_unavailable: ['Photo comparison is unavailable', 'The verification service could not complete the check.', 'Ask the candidate to retry later, or review them manually.'],
    image_size: ['Camera image could not be processed', 'The captured image exceeded the supported size.', 'Ask the candidate to retry, or review them manually.'],
    image_format: ['Camera image could not be read', 'The captured image was not in a supported format.', 'Ask the candidate to retry, or review them manually.'],
    threshold_unconfigured: ['Comparison needs your review', 'A similarity score is available, but automatic matching is not configured.', 'Review the score and candidate details, then make your admission decision.'],
  }
  const [title, explanation, nextStep] = !data ? ['Checking photo verification…', 'The latest result will appear here.', '']
    : data.manual_review_required ? ['Photo attempt limit reached', `All ${data.attempt_limit} photo attempts have been used. Further capture is blocked.`, 'Record a manual review before admitting this candidate.']
    : deleted ? ['Manual review needed', 'Photo verification data was ' + (data.consent === 'expired' ? 'removed after its retention period.' : 'deleted after consent was withdrawn.'), 'Use your interview process to review the candidate before deciding whether to admit them.']
      : data.consent === 'declined' ? ['Candidate chose manual review', 'The candidate did not consent to automatic photo comparison.', 'Review their application details and record your manual review.']
        : data.reason && issues[data.reason] ? issues[data.reason]
          : data.status === 'matched' ? ['Photo comparison completed', 'The camera image meets the configured matching threshold for the application photo.', 'Review the candidate details and admit them when you are ready.']
            : data.status === 'not_identified' ? ['Photos did not meet the matching threshold', 'The similarity is below the configured threshold. This does not automatically reject the candidate.', 'Review the candidate manually or ask them to retake a clearer photo before deciding.']
              : data.status === 'unable_to_verify' ? ['Photo comparison could not be completed', 'No reliable comparison result is available for this check.', 'Ask the candidate to retry, or review them manually.']
                : !data.has_reference ? issues.reference_missing
                  : data.consent === 'accepted' ? ['Waiting for the candidate’s photo', 'Consent received. The candidate can complete the camera check in the waiting room.', 'Wait for the photo comparison, or review the candidate manually.']
                    : ['Waiting for candidate consent', 'The candidate needs to choose photo verification or manual review.', 'Ask the candidate to continue through the waiting-room instructions.']

  return <section className={`identity-verification recruiter-verification is-${tone}`} aria-label="Candidate identification">
    <header className="recruiter-verification-header">
      <div className="recruiter-verification-heading"><span className="recruiter-verification-icon" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="12" cy="10" r="2.5" /><path d="M7.5 17c.5-4 8.5-4 9 0" /></svg></span><h3>Photo verification</h3></div>
      <strong className="recruiter-verification-badge" role="status"><span aria-hidden="true">{tone === 'success' ? '✓' : tone === 'attention' ? '!' : '○'}</span>{data ? identityLabel(data) : 'Checking…'}</strong>
    </header>
    <div className="recruiter-verification-result">
      <div className="recruiter-verification-message"><h4>{data?.reviewed_at ? 'Manual review saved' : title}</h4><p>{data?.reviewed_at ? 'You can now make your admission decision.' : nextStep}</p></div>
      {hasScore && <div className="recruiter-verification-score" aria-label="Face comparison score"><strong>{`${(data!.similarity! * 100).toFixed(1)}%`}</strong><span>Photo similarity</span></div>}
      {data && !deleted && <button className="button-secondary" aria-expanded={reviewOpen} aria-controls={reviewId} onClick={() => { if (!reviewOpen) setNote(data.review_note ?? ''); setReviewOpen(value => !value) }}>{reviewOpen ? 'Close review' : data.reviewed_at ? 'Edit review' : 'Review manually'}</button>}
    </div>
    {data && <>
      <div className="recruiter-verification-meta"><span><i aria-hidden="true" />{data.consent === 'accepted' ? 'Consent received' : data.consent === 'pending' ? 'Consent pending' : data.consent === 'declined' ? 'Manual review requested' : data.consent === 'expired' ? 'Data expired' : 'Consent withdrawn'}</span>
        {data.attempt_limit_reached && <span>Photo attempts exhausted</span>}
        {data.checked_at && <span>Checked <time dateTime={data.checked_at}>{new Date(data.checked_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}</time></span>}
        {!hasScore && <span>No score yet</span>}
      </div>
      {!deleted && <div id={reviewId} className="recruiter-verification-review" hidden={!reviewOpen}>
        <h4>Manual review</h4><p>Record your check. This does not admit the candidate.</p>
        <label>Review note (do not add identity documents)<textarea maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label>
        <div className="identity-actions"><button className="button-secondary" disabled={busy || !note.trim()} onClick={() => void save('review')}>Record manual review</button>
          <button className="button-secondary" disabled={busy} onClick={() => setReviewOpen(false)}>Cancel</button></div>
      </div>}
      <details className="recruiter-verification-details"><summary>Details &amp; data controls</summary>
        <p>{explanation}</p>
        {data.reviewed_at && <p>Manual review: {data.review_note} · {new Date(data.reviewed_at).toLocaleString()}</p>}
        <p>This checks photo resemblance. It does not establish official identity or liveness, and never admits or rejects a candidate automatically.</p>
        {hasScore && <p>Cosine similarity scaled by 100, not a probability of identity. {typeof data.threshold === 'number' ? `Matching threshold: ${(data.threshold * 100).toFixed(1)}%.` : 'No automatic matching threshold is configured.'}</p>}
        {!deleted && <div className="recruiter-verification-delete"><p>Deleting removes Distill’s photo link and verification data. The original Google Drive upload remains with its owner.</p><button className="button-secondary" disabled={busy} onClick={() => void save('delete')}>Delete identification data</button></div>}
      </details>
    </>}
    {error && <p role="alert">Verification status unavailable: {error}</p>}
  </section>
}
