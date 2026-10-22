import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { usePreviewTracks } from '@livekit/components-react'
import type { LocalUserChoices } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { checkIdentityFrame, identityLabel, identityReasons, loadCandidateIdentity, saveIdentityConsent, verifyIdentity } from './identityApi'
import { advanceFaceCountdown } from './faceCountdown'
import { ApiError } from '../../services/api'
import type { CandidateIdentity, IdentityStatus } from './identityApi'
import './IdentityVerification.css'

export default function CandidateFaceVerification({ token, choices, previewTarget }: {
  token: string; choices: LocalUserChoices; previewTarget: HTMLDivElement | null
}) {
  const [data, setData] = useState<CandidateIdentity | null>(null)
  const [camera, setCamera] = useState(false)
  const [popupOpen, setPopupOpen] = useState(true)
  const dialog = useRef<HTMLDialogElement>(null)
  const capturePending = useRef(false)
  const countdownTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const frameController = useRef<AbortController | null>(null)
  const countdownStartedAt = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<number | null>(null)
  const [frameIssue, setFrameIssue] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const video = useRef<HTMLVideoElement>(null)
  const lock = useRef(false)
  const mounted = useRef(true)
  const stopCapture = useCallback(() => {
    if (countdownTimer.current) clearTimeout(countdownTimer.current)
    frameController.current?.abort()
    frameController.current = null
    countdownTimer.current = null
    countdownStartedAt.current = null
    capturePending.current = false
    setCountdown(null)
    setCamera(false)
  }, [])
  const reportCameraError = useCallback((caught: Error) => { setError(caught.message); stopCapture() }, [stopCapture])
  const tracks = usePreviewTracks({ audio: false, video: camera ? { deviceId: choices.videoDeviceId || 'default' } : false }, reportCameraError)
  const track = camera ? tracks?.find(item => item.kind === Track.Kind.Video && item.mediaStreamTrack.readyState === 'live') : undefined
  useEffect(() => {
    const element = dialog.current
    if (popupOpen && element && !element.open) element.showModal()
    else if (!popupOpen && element?.open) element.close()
  }, [popupOpen])
  useEffect(() => {
    const element = video.current
    if (!element || !track) return
    track.attach(element)
    return () => { track.detach(element) }
  }, [track, previewTarget])
  useEffect(() => {
    if (camera) window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [camera])
  useEffect(() => () => {
    if (countdownTimer.current) clearTimeout(countdownTimer.current)
    frameController.current?.abort()
    capturePending.current = false
  }, [])
  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void loadCandidateIdentity(token, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value); setError('') }
    }).catch(caught => { if (!controller.signal.aborted) setError(caught.message) })
    return () => { mounted.current = false; controller.abort() }
  }, [token, revision])

  async function perform(action: () => Promise<IdentityStatus>) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try {
      const result = await action()
      if (mounted.current) {
        setData(current => current ? { ...current, ...result } : current)
        if (result.reason !== 'multiple_faces' || result.attempt_limit_reached) stopCapture()
      }
      return result
    } catch (caught) { if (mounted.current) { stopCapture(); await refreshAttemptStatus(caught); if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not verify your photo.') } }
    finally { lock.current = false; if (mounted.current) setBusy(false) }
  }

  async function refreshAttemptStatus(caught: unknown) {
    if (!(caught instanceof ApiError) || caught.status !== 429) return
    try {
      const latest = await loadCandidateIdentity(token)
      if (mounted.current) setData(latest)
    } catch { /* The server still enforces the limit if refreshing fails. */ }
  }

  function cameraFrame() {
    const element = video.current
    if (!element?.videoWidth || element.readyState < 2) return null
    const canvas = document.createElement('canvas')
    const scale = Math.min(1, 1280 / Math.max(element.videoWidth, element.videoHeight))
    canvas.width = Math.round(element.videoWidth * scale); canvas.height = Math.round(element.videoHeight * scale)
    canvas.getContext('2d')!.drawImage(element, 0, 0, canvas.width, canvas.height)
    const frame = canvas.toDataURL('image/jpeg', 0.9).split(',')[1]
    canvas.width = canvas.height = 0
    return frame
  }

  async function consentAndCapture() {
    if (!data) return
    const result = await perform(() => saveIdentityConsent(token, 'accepted', data.consent_version))
    if (result?.consent === 'accepted' && !result.attempt_limit_reached && mounted.current) startCapture()
  }

  function startCapture() {
    if (!data || data.attempt_limit_reached || busy) return
    setPopupOpen(false)
    capturePending.current = true
    countdownStartedAt.current = null
    setCountdown(null)
    setFrameIssue(null)
    setError('')
    setCamera(true)
  }

  function captureWhenReady() {
    if (!capturePending.current || !video.current?.videoWidth || frameController.current) return
    const controller = new AbortController()
    frameController.current = controller
    let previousSampleAt: number | null = null
    async function checkPreview() {
      if (controller.signal.aborted || !capturePending.current) return
      const frame = cameraFrame()
      const sampledAt = performance.now()
      try {
        if (!frame) {
          countdownStartedAt.current = null
          setCountdown(null)
          setFrameIssue('no_face')
        } else {
          const result = await checkIdentityFrame(token, frame, controller.signal)
          if (controller.signal.aborted || !capturePending.current) return
          // A stalled preview or slow check cannot count as uninterrupted visibility.
          const stale = performance.now() - sampledAt > 1500
          if (stale || (previousSampleAt !== null && sampledAt - previousSampleAt > 1500)) countdownStartedAt.current = null
          const next = advanceFaceCountdown(countdownStartedAt.current, stale ? null : result.face_count, sampledAt)
          previousSampleAt = sampledAt
          countdownStartedAt.current = next.startedAt
          setCountdown(next.seconds)
          setFrameIssue(stale ? 'checking_camera' : result.reason)
          if (next.ready) {
            capturePending.current = false
            frameController.current = null
            setCountdown(null)
            // Compare the exact frame that passed the final single-face check.
            const comparison = await perform(() => verifyIdentity(token, frame))
            if (comparison?.reason === 'multiple_faces' && !comparison.attempt_limit_reached && mounted.current) {
              countdownStartedAt.current = null
              setFrameIssue('multiple_faces')
              capturePending.current = true
              captureWhenReady()
            }
            return
          }
        }
      } catch (caught) {
        if (controller.signal.aborted) return
        stopCapture()
        await refreshAttemptStatus(caught)
        if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not check your camera. Please try again.')
        return
      }
      if (!controller.signal.aborted && capturePending.current) countdownTimer.current = setTimeout(() => void checkPreview(), 500)
    }
    void checkPreview()
  }

  const canVerify = data?.has_reference && data.available && data.consent === 'accepted' && !data.attempt_limit_reached
  const limitMessage = data?.manual_review_required
    ? `You’ve reached the ${data.attempt_limit}-photo limit. You can’t take more photos. Your interviewer needs to review you manually.`
    : data?.reviewed_at ? 'No more photos can be taken. Your interviewer has recorded a manual review.'
      : `You’ve used all ${data?.attempt_limit} photo attempts. No more photos can be taken. Wait for your interviewer’s admission decision.`
  const content = <section className="identity-verification" aria-label="Photo verification">
    <h2 id="photo-verification-title">Verify your application photo</h2>
    {!data ? <p role="status">{error ? 'Verification could not be loaded.' : 'Loading verification…'}</p> : <>
      <strong role="status">{identityLabel(data)}</strong>
      {data.attempt_limit_reached && <p role="status">{limitMessage}</p>}
      {data.reason && !data.attempt_limit_reached && <p>{identityReasons[data.reason] ?? 'Ask your recruiter for manual review.'}</p>}
      {data.consent === 'withdrawn' || data.consent === 'expired' ? <p>Identification data was {data.consent === 'expired' ? 'removed after its retention period' : 'deleted'}. Your recruiter can review you manually.</p>
        : !data.has_reference ? <p>Your recruiter can review you manually.</p> : <>
          <p>{data.consent_text}</p>
          <p>After you agree, this window will close and the page will scroll to the top. Your camera will appear in the waiting room. The five-second countdown starts when only your face is visible. If someone enters the frame or your face disappears, the countdown stops and starts again from five when you are alone. Use clear lighting.</p>
          <p>The recruiter receives the comparison result and similarity score before admitting you. Camera frames are not saved.</p>
          <p>Identification data expires {data.expires_at ? new Date(data.expires_at).toLocaleString() : `within ${data.retention_days} days`}.</p>
          {!data.available && <p>Automatic comparison is unavailable. You can continue with manual review.</p>}
          {data.consent !== 'accepted' ? <div className="identity-actions">
            <button className="button-primary" disabled={busy || !data.available || data.attempt_limit_reached} onClick={() => void consentAndCapture()}>Agree and take my photo</button>
            <button className="button-secondary" disabled={busy} onClick={() => void perform(() => saveIdentityConsent(token, 'declined', data.consent_version))}>Decline · use manual review</button>
          </div> : <>
            {canVerify && !camera && <button className="button-primary" disabled={busy} onClick={startCapture}>{data.checked_at ? 'Retake and compare photo' : 'Take and compare photo'}</button>}
            {busy && <p role="status">Comparing your photo…</p>}
            {camera && !busy && <p role="status">Look at the camera. Taking your photo automatically…</p>}
            <button className="button-secondary" disabled={busy} onClick={() => void perform(() => saveIdentityConsent(token, 'declined', data.consent_version))}>Use manual review instead</button>
          </>}
          <button className="button-secondary" disabled={busy} onClick={() => void perform(() => saveIdentityConsent(token, 'withdrawn', data.consent_version))}>Withdraw and delete identification data</button>
        </>}
      {data.reviewed_at && <p>Recruiter manual review recorded.</p>}
    </>}
    {error && <p role="alert">{error}</p>}
    {!data && error && <button className="button-secondary" onClick={() => setRevision(value => value + 1)}>Retry</button>}
    <button className="button-secondary" disabled={busy || camera} onClick={() => setPopupOpen(false)}>Continue to waiting room</button>
  </section>
  return <>
    <section className="candidate-card identity-verification" aria-label="Photo verification summary">
      <h2>Application photo verification</h2>
      <strong role="status">{data ? identityLabel(data) : 'Checking verification…'}</strong>
      {data?.attempt_limit_reached && <p role="status">{limitMessage}</p>}
      {busy && !popupOpen && <p role="status">Comparing your photo…</p>}
      {camera && <p role="status">{busy ? 'Comparing your photo…' : countdown === null ? frameIssue === 'multiple_faces' ? 'Please be alone in the camera frame. The countdown will restart automatically.' : 'Waiting until only your face is visible…' : `Taking your photo in ${countdown} seconds…`}</p>}
      {!popupOpen && data?.reason && !data.attempt_limit_reached && <p>{identityReasons[data.reason] ?? 'Ask your recruiter for manual review.'}</p>}
      {!popupOpen && error && <p role="alert">{error}</p>}
      {canVerify && !camera && <button className="button-primary" disabled={busy} onClick={startCapture}>{data.checked_at ? 'Retake and compare photo' : 'Take and compare photo'}</button>}
      <button className="button-secondary" disabled={busy || camera} onClick={() => setPopupOpen(true)}>Open photo verification</button>
    </section>
    <dialog ref={dialog} className="identity-dialog" aria-labelledby="photo-verification-title"
      onCancel={event => { event.preventDefault(); if (!busy && !camera) setPopupOpen(false) }}>
      {popupOpen && content}
    </dialog>
    {camera && previewTarget && createPortal(<div className="identity-waiting-camera">
      <video ref={video} autoPlay playsInline muted onLoadedData={captureWhenReady} aria-label="Face verification camera preview" />
      <div className="identity-capture-overlay">
        <p role="status">{busy ? 'Checking your photo…' : countdown !== null ? `Photo in ${countdown} seconds` : frameIssue === 'multiple_faces' ? 'Please be alone in the frame' : 'Looking for your face…'}</p>
        {frameIssue === 'multiple_faces' ? <p role="alert">More than one face is visible. Please be alone in the frame. The countdown will restart from five automatically.</p>
          : frameIssue === 'no_face' ? <p role="alert">Position your face in the camera. The countdown will start when only your face is visible.</p>
            : <p>Stay alone in the frame and hold still. The timer restarts if someone enters.</p>}
        <button className="button-secondary" disabled={busy} onClick={stopCapture}>Cancel photo capture</button>
      </div>
    </div>, previewTarget)}
  </>
}
