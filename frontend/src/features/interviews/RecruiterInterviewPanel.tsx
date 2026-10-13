import { useCallback, useEffect, useRef, useState } from 'react'
import type { LocalUserChoices } from '@livekit/components-react'
import type { SavedInterviewee } from './api'
import InterviewRoom, { DeviceCheck } from './InterviewRoom'
import {
  admitCandidate, endInterview, joinRecruiterInterview, loadInterviewStatus, startInterview,
} from './sessionApi'
import type { RecruiterInterviewStatus, RoomCredentials } from './sessionApi'

function RecruiterSession({ token, jobId, candidate }: {
  token: string; jobId: string; candidate: SavedInterviewee
}) {
  const [status, setStatus] = useState<RecruiterInterviewStatus | null>(null)
  const [choices, setChoices] = useState<LocalUserChoices | null>(null)
  const [credentials, setCredentials] = useState<RoomCredentials | null>(null)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
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
  return <>
    <p><strong>{candidate.name}</strong> · {candidate.email}</p>
    <p role="status">Interview: {status?.status ?? 'checking…'}</p>
    {pollError && <p role="alert">Status check failed: {pollError}</p>}
    {error && <p role="alert">{error}</p>}
    {message && !finished && status?.status !== 'closing' && <p role="status">{message}</p>}
    {status?.status === 'closing' && <p>Room closure is pending. Retry End interview.</p>}
    {!finished && status && status.status !== 'closing' && !credentials && !busy && <>
      {!choices ? <DeviceCheck name="Recruiter" onReady={value => void startOrJoin(value)} />
        : <div className="interview-test-actions">
          <button className="button-primary" onClick={() => void startOrJoin(choices)}>{active ? 'Rejoin interview' : 'Start interview'}</button>
          <button className="button-secondary" onClick={() => setChoices(null)}>Check devices again</button>
        </div>}
    </>}
    {busy && <p role="status">Working…</p>}
    {credentials && choices && <InterviewRoom credentials={credentials} choices={choices}
      onLeave={leave} onError={reportError} onConnectionChange={reportConnection} />}
    <div className="interview-test-actions">
      {active && <button className="button-primary" disabled={busy || !connected || status.candidate_admitted}
        onClick={() => void perform(async () => {
          const next = await admitCandidate(token, jobId, candidate.application_id)
          if (mounted.current) setStatus(next)
        })}>{status.candidate_admitted ? 'Candidate admitted' : 'Admit candidate'}</button>}
      {(active || status?.status === 'closing') && <button className="button-secondary" disabled={busy}
        onClick={() => {
          if (status?.status !== 'closing' && !window.confirm('End this interview for everyone?')) return
          void perform(async () => {
            const next = await endInterview(token, jobId, candidate.application_id)
            if (mounted.current) { setStatus({ ...next, candidate_admitted: false }); setCredentials(null); setConnected(false) }
          })
        }}>{status?.status === 'closing' ? 'Retry End interview' : 'End interview for everyone'}</button>}
    </div>
    <p>Leave exits your call. End interview finishes it for both participants. This test UI does not record audio or video.</p>
  </>
}

export default function RecruiterInterviewPanel({ token, jobId, members }: {
  token: string; jobId: string; members: SavedInterviewee[]
}) {
  const [chosenId, setChosenId] = useState('')
  const candidate = members.find(member => member.application_id === chosenId) ?? members[0]
  return <section className="interview-test-panel" aria-labelledby="live-interview-heading">
    <h3 id="live-interview-heading">Live interview test</h3>
    <p>Select a saved candidate with an active invitation. Switching candidates exits the current call without ending it.</p>
    {candidate ? <>
      <label>Candidate<select value={candidate.application_id} onChange={event => setChosenId(event.target.value)}>
        {members.map(member => <option key={member.application_id} value={member.application_id}>{member.name}</option>)}
      </select></label>
      <RecruiterSession key={candidate.application_id} token={token} jobId={jobId} candidate={candidate} />
    </> : <p>Save your candidate selection first.</p>}
  </section>
}
