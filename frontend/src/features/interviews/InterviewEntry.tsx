import { useCallback, useEffect, useRef, useState } from 'react'
import type { LocalUserChoices } from '@livekit/components-react'
import { ApiError } from '../../services/api'
import InterviewRoom, { DeviceCheck } from './InterviewRoom'
import { joinCandidateInterview, loadCandidateInterviewStatus } from './sessionApi'
import type { CandidateInterviewStatus, RoomCredentials } from './sessionApi'

type Entry = {
  job_title: string
  instructions: string
  expires_at: string
  interview_ready: boolean
}

export default function InterviewEntry() {
  const [token] = useState(() => window.location.hash.slice(1))
  const [entry, setEntry] = useState<Entry | null>(null)
  const [status, setStatus] = useState<CandidateInterviewStatus | null>(null)
  const [choices, setChoices] = useState<LocalUserChoices | null>(null)
  const [credentials, setCredentials] = useState<RoomCredentials | null>(null)
  const [busy, setBusy] = useState(false)
  const [invalidLink, setInvalidLink] = useState(false)
  const [pollError, setPollError] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState(() => /^[A-Za-z0-9_-]{43}$/.test(token)
    ? '' : 'This interview link is invalid or no longer active.')
  const mounted = useRef(true)
  const locked = useRef(false)
  const terminal = useRef(false)
  const reportError = useCallback((caught: Error) => setError(caught.message), [])
  const leave = useCallback(() => {
    setCredentials(null)
    setMessage('You left the call. You can rejoin while the interview is active.')
  }, [])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
      return
    }

    async function load() {
      try {
        const response = await fetch('/api/interviews/entry', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
          signal: controller.signal,
          cache: 'no-store',
          referrerPolicy: 'no-referrer',
        })
        if (!response.ok) {
          throw new Error(response.status === 410
            ? 'This interview link is invalid or no longer active.'
            : 'Could not verify the interview link. Please try again later.')
        }
        const data: Entry = await response.json()
        if (!controller.signal.aborted) setEntry(data)
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : 'Could not verify the interview link.')
        }
      }
    }
    void load()
    return () => { mounted.current = false; controller.abort() }
  }, [token])

  useEffect(() => {
    if (!entry) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await loadCandidateInterviewStatus(token, controller.signal)
        if (controller.signal.aborted) return
        setStatus(next)
        setPollError('')
        terminal.current = ['closing', 'completed', 'cancelled'].includes(next.status)
        if (terminal.current || !next.can_join) setCredentials(null)
      } catch (caught) {
        if (controller.signal.aborted) return
        if (caught instanceof ApiError && (caught.status === 410 || caught.status === 404)) {
          terminal.current = true
          setInvalidLink(true)
          setCredentials(null)
          setError(caught.message)
          return
        }
        setPollError(caught instanceof Error ? caught.message : 'Could not check the interview status.')
      } finally {
        if (!controller.signal.aborted && !terminal.current) timer = setTimeout(() => void poll(), 3000)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [entry, token])

  async function join() {
    if (locked.current || !status?.can_join || terminal.current) return
    locked.current = true
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const next = await joinCandidateInterview(token)
      if (mounted.current && !terminal.current) setCredentials(next)
    } catch (caught) {
      if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not join the interview.')
    } finally {
      locked.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const finished = status?.status === 'completed' || status?.status === 'cancelled'
  return <main className="candidate-interview-page">
    <h1>Join your interview</h1>
    {error && <p role="alert">{error}</p>}
    {!entry && !error && <p role="status">Checking your interview link…</p>}
    {entry && <>
      <h2>{entry.job_title}</h2>
      <p>{entry.instructions}</p>
      <p>Your link is valid until {new Date(entry.expires_at).toLocaleString()}.</p>
      {pollError && <p role="alert">Status check failed: {pollError}</p>}
      {finished ? <p role="status">This interview has finished.</p>
        : status?.status === 'closing' ? <p role="status">The recruiter is ending this interview.</p>
        : !invalidLink && <>
          {!choices && <DeviceCheck name="Candidate" onReady={value => { setChoices(value); setError('') }} />}
          {choices && !credentials && <>
            <p role="status">{!status ? 'Checking interview status…' : status.can_join
              ? 'The recruiter admitted you. You can join now.'
              : status.status === 'waiting_for_recruiter' ? 'Waiting for the recruiter to start the interview.'
                : 'Waiting for the recruiter to admit you.'}</p>
            <div className="interview-test-actions">
              <button className="button-primary" disabled={busy || !status?.can_join} onClick={() => void join()}>{busy ? 'Joining…' : 'Join interview'}</button>
              <button className="button-secondary" disabled={busy} onClick={() => setChoices(null)}>Check devices again</button>
            </div>
          </>}
          {credentials && choices && <InterviewRoom credentials={credentials} choices={choices}
            onLeave={leave} onError={reportError} />}
          {message && <p role="status">{message}</p>}
          <p>Leave exits your call; the recruiter ends the interview. Audio and video are not being recorded in this test.</p>
        </>}
    </>}
  </main>
}
