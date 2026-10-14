import { useCallback, useEffect, useRef, useState } from 'react'
import type { LocalUserChoices } from '@livekit/components-react'
import { loadInvitations } from './api'
import type { SavedInterviewee, InvitationStatus } from './api'
import { Link, useSearchParams } from 'react-router-dom'
import InterviewPreparation from './InterviewPreparation'
import InterviewWaitingRoom from './InterviewWaitingRoom'
import InterviewRoom, { StageRoom } from './InterviewRoom'
import {
  admitCandidate, endInterview, joinRecruiterInterview, loadInterviewStatus, startInterview,
} from './sessionApi'
import type { RecruiterInterviewStatus, RoomCredentials } from './sessionApi'
import RecruiterChat from '../jd/RecruiterChat'
import InterviewStage from './InterviewStage'

const ignoreSavedJD = () => {}

function RecruiterSession({ token, userId, jobId, candidate, members, invitation, invitationLoading, jobTitle, jobNumber, onCandidate }: {
  token: string; userId: string; jobId: string; candidate: SavedInterviewee
  jobTitle: string; jobNumber: number; members: SavedInterviewee[]; invitation?: InvitationStatus; invitationLoading: boolean; onCandidate: (id: string) => void
}) {
  const [status, setStatus] = useState<RecruiterInterviewStatus | null>(null)
  const [choices, setChoices] = useState<LocalUserChoices | null>(null)
  const [credentials, setCredentials] = useState<RoomCredentials | null>(null)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [prepared, setPrepared] = useState(false)
  const [previewRevision, setPreviewRevision] = useState(0)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [message, setMessage] = useState('')
  const locked = useRef(false)
  const generation = useRef(0)
  const mounted = useRef(true)
  const reportError = useCallback((caught: Error) => setError(caught.message), [])
  const reportConnection = useCallback((value: boolean) => setConnected(value), [])
  const leave = useCallback(() => {
    setCredentials(null)
    setConnected(false)
    setMessage('You left the call. The interview stays active until you choose End interview.')
  }, [])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const version = generation.current
      try {
        if (!locked.current) {
          const next = await loadInterviewStatus(token, jobId, candidate.application_id, controller.signal)
          if (!controller.signal.aborted && version === generation.current) {
            setStatus(next)
            setPollError('')
            if (['closing', 'completed', 'cancelled'].includes(next.status)) {
              setCredentials(null)
              setConnected(false)
            }
          }
        }
      } catch (caught) {
        if (!controller.signal.aborted) setPollError(caught instanceof Error ? caught.message : 'Could not load interview status.')
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 3000)
      }
    }
    void poll()
    return () => { mounted.current = false; controller.abort(); clearTimeout(timer) }
  }, [token, jobId, candidate.application_id])

  async function perform(action: () => Promise<void>) {
    if (locked.current) return
    locked.current = true
    generation.current += 1
    setBusy(true)
    setError('')
    setMessage('')
    try { await action() } catch (caught) {
      if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not complete the request.')
    } finally {
      locked.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function startOrJoin(deviceChoices: LocalUserChoices) {
    setChoices(deviceChoices)
    setPrepared(true)
    await perform(async () => {
      if (status?.status === 'in_progress') {
        const result = await joinRecruiterInterview(token, jobId, candidate.application_id)
        if (mounted.current) setCredentials(result)
      } else {
        const result = await startInterview(token, jobId, candidate.application_id)
        if (!mounted.current) return
        setStatus({ ...result, candidate_admitted: false })
        setCredentials(result)
      }
    })
  }

  const active = status?.status === 'in_progress'
  const finished = status?.status === 'completed' || status?.status === 'cancelled'
  function finishInterview() {
    if (!active && status?.status !== 'closing') return
    if (status?.status !== 'closing' && !window.confirm('End this interview for everyone?')) return
    void perform(async () => {
      const next = await endInterview(token, jobId, candidate.application_id)
      if (mounted.current) { setStatus({ ...next, candidate_admitted: false }); setCredentials(null); setConnected(false); setPrepared(false); setChoices(null) }
    })
  }
  function recheckDevices() {
    setCredentials(null)
    setConnected(false)
    setChoices(null)
    setPrepared(false)
    setPreviewRevision(value => value + 1)
  }
  const notices = <>
    {pollError && <p role="alert">Status check failed: {pollError}</p>}
    {error && <p role="alert">{error}</p>}
    {message && !finished && <p role="status">{message}</p>}
    {status?.status === 'closing' && <p>Room closure is pending. Retry End interview.</p>}
    {finished && <p role="status">This interview has finished.</p>}
  </>
  const admitted = active && status.candidate_admitted
  const stage = admitted && credentials && choices
  async function admit() {
    await perform(async () => {
      const next = await admitCandidate(token, jobId, candidate.application_id)
      if (mounted.current) setStatus(next)
    })
  }
  const lobby = <section className="interview-lobby" aria-labelledby="live-interview-heading">
    <header className="lobby-heading"><div><h2 id="live-interview-heading">Live interview</h2><p>{prepared ? connected ? 'Your waiting room is open. Admit the candidate when you are ready.' : busy || credentials ? 'Connecting to your waiting room…' : 'Rejoin your waiting room or recheck your devices.' : 'Check your camera and microphone before you meet the candidate.'}</p></div><ol className="lobby-steps" aria-label="Interview steps">{['Preparation', 'Waiting room', 'Interview'].map((label, index) => <li key={label} className={(prepared ? index === 1 : index === 0) ? 'is-current' : ''} aria-current={(prepared ? index === 1 : index === 0) ? 'step' : undefined}><span>{prepared && index === 0 ? '✓' : index + 1}</span>{label}</li>)}</ol></header>
    {(pollError || error || message || finished || status?.status === 'closing') && <div className="lobby-notices">{notices}</div>}
    {!prepared ? <InterviewPreparation key={previewRevision} candidate={candidate} members={members} invitation={invitation} status={status?.status ?? 'checking'} busy={busy}
      canContinue={!invitationLoading && invitation?.link_status === 'active' && !!status && ['not_started', 'ready', 'in_progress'].includes(status.status)}
      onCandidate={onCandidate} onContinue={value => void startOrJoin(value)} onRecheck={() => setPreviewRevision(value => value + 1)} />
      : credentials ? <InterviewWaitingRoom candidate={candidate} jobTitle={jobTitle} jobNumber={jobNumber} connected={connected} busy={busy} startedAt={status && 'started_at' in status ? status.started_at : null} onAdmit={() => void admit()} onRecheck={recheckDevices} onLeave={recheckDevices} onError={reportError} />
      : <div className="lobby-content waiting-content"><div className="lobby-preview-column"><div className="preparation-video"><div className="lobby-placeholder"><div className="stage-person" /><strong>{busy ? 'Opening waiting room…' : 'You are disconnected'}</strong><p>{candidate.name} · Candidate</p></div></div></div><div className="lobby-info-column"><div className="lobby-card"><strong>{candidate.name}</strong><p>{candidate.email}</p><p>{jobTitle} · JD {jobNumber}</p></div><div className="lobby-primary-action"><button className="lobby-primary" disabled={busy || !choices || finished || status?.status === 'closing'} onClick={() => choices && void startOrJoin(choices)}>{busy ? 'Connecting…' : 'Rejoin waiting room'}</button><button className="lobby-secondary" disabled={busy} onClick={recheckDevices}>Recheck devices</button></div></div></div>}
    {(active || status?.status === 'closing') && <div className="lobby-end"><button disabled={busy} onClick={finishInterview}>{status?.status === 'closing' ? 'Retry End interview' : 'End interview for everyone'}</button></div>}
  </section>
  const content = stage ? <InterviewStage candidate={candidate} jobTitle={jobTitle} jobNumber={jobNumber}
    status={status.status} startedAt={status.started_at} endedAt={status.completed_at} connected={connected} canEnd={active} busy={busy} onEnd={finishInterview} onClose={recheckDevices}
    video={<StageRoom candidateName={candidate.name} onEnd={finishInterview} canEnd={active && !busy} onError={reportError} />}
    assistant={<RecruiterChat key={status.id} token={token} userId={userId} onSaved={ignoreSavedJD} interviewSessionId={status.id} interviewCandidateName={candidate.name} />}
    notices={pollError || error || message ? notices : null} /> : lobby
  return credentials && choices ? <InterviewRoom credentials={credentials} choices={choices} onLeave={leave} onError={reportError} onConnectionChange={reportConnection}>{content}</InterviewRoom> : content
}

export default function RecruiterInterviewPanel({ token, userId, jobId, members, jobTitle, jobNumber }: {
  token: string; userId: string; jobId: string; members: SavedInterviewee[]; jobTitle: string; jobNumber: number
}) {
  const [params, setParams] = useSearchParams()
  const [invitations, setInvitations] = useState<InvitationStatus[]>([])
  const [invitationLoading, setInvitationLoading] = useState(true)
  const [invitationError, setInvitationError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void loadInvitations(token, jobId, controller.signal).then(next => {
      if (!controller.signal.aborted) { setInvitations(next); setInvitationLoading(false); setInvitationError('') }
    }).catch(caught => {
      if (!controller.signal.aborted) { setInvitationError(caught instanceof Error ? caught.message : 'Could not load invitations.'); setInvitationLoading(false) }
    })
    return () => controller.abort()
  }, [token, jobId, revision])
  const candidate = members.find(member => member.application_id === params.get('candidate')) ?? members[0]
  function chooseCandidate(id: string) { setParams(current => { current.set('job', jobId); current.set('candidate', id); return current }) }
  if (!candidate) return <section className="interview-lobby lobby-empty"><h2>Live interview</h2><p>No saved candidates yet.</p><Link to={`/recruiter/cv-screening?job=${encodeURIComponent(jobId)}#interview-shortlist`}>Build your interview shortlist in CV Screening →</Link></section>
  return <>
    {invitationError && <div className="lobby-notices" role="alert">{invitationError}</div>}
    <RecruiterSession key={candidate.application_id} token={token} userId={userId} jobId={jobId} candidate={candidate} members={members} invitation={invitations.find(item => item.application_id === candidate.application_id)} invitationLoading={invitationLoading} jobTitle={jobTitle} jobNumber={jobNumber} onCandidate={chooseCandidate} />
    <div className="recruitment-links"><Link to={`/recruiter/invitations?job=${encodeURIComponent(jobId)}`}>Manage candidate invitations</Link><button disabled={invitationLoading} onClick={() => { setInvitationLoading(true); setRevision(value => value + 1) }}>Refresh invitation status</button></div>
  </>
}
