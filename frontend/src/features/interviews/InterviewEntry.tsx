import { useCallback, useEffect, useRef, useState } from 'react'
import type { LocalUserChoices } from '@livekit/components-react'
import { ApiError } from '../../services/api'
import InterviewRoom from './InterviewRoom'
import DeviceCheck from './DeviceCheck'
import { joinCandidateInterview, loadCandidateInterviewStatus } from './sessionApi'
import type { CandidateInterviewStatus, RoomCredentials } from './sessionApi'
import CandidateInterviewView from './CandidateInterviewView'
import type { CandidatePhase } from './CandidateInterviewView'

type Entry = {
  candidate_name: string
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
  const [checkingDevices, setCheckingDevices] = useState(false)
  const [credentials, setCredentials] = useState<RoomCredentials | null>(null)
  const [connected, setConnected] = useState(false)
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
    setConnected(false)
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
  const phase: CandidatePhase = invalidLink || (!entry && error) ? 'unavailable'
    : finished ? 'finished' : status?.status === 'closing' ? 'ending'
      : credentials && choices ? 'call' : !entry ? 'checking' : !choices || checkingDevices ? 'devices'
        : status?.can_join ? 'ready' : 'waiting'
  const statusText = phase === 'call' ? connected ? 'Connected' : 'Connecting…'
    : phase === 'finished' ? 'Interview finished' : phase === 'ending' ? 'Interview ending'
      : phase === 'unavailable' ? 'Invitation unavailable' : phase === 'checking' ? 'Checking invitation…'
        : phase === 'devices' ? 'Check your devices' : !status ? 'Checking interview status…'
          : status.can_join ? 'The recruiter admitted you. You can join now.'
            : status.status === 'waiting_for_recruiter' ? 'Waiting for the recruiter to start the interview.'
              : 'Waiting for the recruiter to admit you.'
  return <CandidateInterviewView phase={phase} statusText={statusText} jobTitle={entry?.job_title}
    instructions={entry?.instructions} expiresAt={entry?.expires_at} busy={busy}
    error={[error, pollError && `Status check failed: ${pollError}`].filter(Boolean).join(' ')} notice={message}
    onJoin={() => void join()} onRecheck={() => setCheckingDevices(true)}
    canReturnToWaiting={!!choices} onWaiting={() => setCheckingDevices(false)}
    deviceCheck={entry && phase === 'devices' ? <DeviceCheck name={entry.candidate_name} choices={choices} onReady={value => { setChoices(value); setCheckingDevices(false); setError('') }} /> : undefined}
    call={credentials && choices ? <InterviewRoom credentials={credentials} choices={choices}
      stage participantRole="candidate" candidateName="Interviewer"
      onConnectionChange={setConnected} onLeave={leave} onError={reportError} /> : undefined} />
}
