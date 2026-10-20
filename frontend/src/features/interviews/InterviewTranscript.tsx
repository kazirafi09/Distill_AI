import { useEffect, useRef, useState } from 'react'
import { loadTranscript } from './sessionApi'
import type { TranscriptSnapshot } from './sessionApi'
import { groupTranscriptTurns, visibleInterimTurns } from './transcriptGrouping'
import './InterviewStage.css'

function timestamp(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}.${Math.floor(milliseconds % 1000 / 100)}`
}

export default function InterviewTranscript({ token, jobId, applicationId }: {
  token: string; jobId: string; applicationId: string
}) {
  const [saved, setSaved] = useState<{ scope: string; data: TranscriptSnapshot } | null>(null)
  const [error, setError] = useState('')
  const log = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const [following, setFollowing] = useState(true)
  const scope = `${jobId}:${applicationId}`
  const data = saved?.scope === scope ? saved.data : null
  const utterances = groupTranscriptTurns(data?.turns ?? [])
  const interim = !error && data?.transcript?.status === 'capturing'
    ? visibleInterimTurns(data.turns, data.interim_turns ?? []) : []

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      let delay = 2000
      try {
        const next = await loadTranscript(token, jobId, applicationId, controller.signal)
        if (!controller.signal.aborted) { setSaved({ scope, data: next }); setError('') }
        if (next.capture.status === 'listening' || next.interim_turns?.length) delay = 400
        else if (['connecting', 'reconnecting', 'stopping'].includes(next.capture.status)) delay = 1000
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load transcript.')
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), delay)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, applicationId, scope])

  const lastTurn = data?.turns.at(-1)?.id
  const interimRevision = interim.map(turn => `${turn.stream_id}:${turn.text}`).join('|')
  const captureLabels = { inactive: 'Capture inactive', connecting: 'Connecting transcription…',
    waiting_for_microphone: 'Waiting for microphone', listening: 'Transcribing', stopping: 'Saving final text…',
    reconnecting: 'Reconnecting transcription…', failed: 'Transcription disconnected' }
  useEffect(() => {
    if (follow.current && log.current) log.current.scrollTop = log.current.scrollHeight
  }, [lastTurn, interimRevision])

  return <section className="stage-transcript" aria-label="Interview transcript">
    <header><strong>Interview transcript</strong><span className={`stage-badge${data?.capture.status === 'listening' ? '' : ' muted'}`}>{data ? captureLabels[data.capture.status] : 'Loading…'}</span></header>
    <p className="stage-transcript-info">Live text may change as you speak. Final text is saved automatically. Times are relative to each microphone stream.</p>
    {data?.transcript && <p className="stage-transcript-info">Version {data.transcript.version} · {data.transcript.status === 'capturing' ? 'Live capture' : data.transcript.status.replaceAll('_', ' ')} · {utterances.length} utterances / {data.turns.length} saved segments</p>}
    {data?.transcript?.error_code && <p className="stage-transcript-error" role="alert">Transcription issue: {data.transcript.error_code.replaceAll('_', ' ').toLowerCase()}. Saved text is retained. {data.capture.status === 'reconnecting' ? 'Reconnecting automatically.' : 'If capture has stopped while the interview is active, unmute your microphone or rejoin to retry.'}</p>}
    {data?.transcript?.quality_flags.includes('FINALIZATION_PENDING') && <p className="stage-transcript-info">Capture stopped. Transcript timing and completeness still need review.</p>}
    {error && <p className="stage-transcript-error" role="alert">{error} {data ? 'Previously loaded text is retained; retrying.' : 'Retrying.'}</p>}
    {data?.has_earlier_turns && <p className="stage-transcript-info">Showing the latest 500 saved segments. Earlier segments remain stored.</p>}
    <div className="stage-transcript-turns" ref={log} onScroll={event => {
      const element = event.currentTarget
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48
      setFollowing(follow.current)
    }}>
      {!data && !error && <p role="status">Loading transcript…</p>}
      {data && !data.turns.length && !interim.length && <div className="stage-empty"><strong>Waiting for speech</strong><p>Transcription starts when the candidate is admitted. Speak with a microphone enabled; live text will appear here.</p></div>}
      {utterances.map(turn => <article className="stage-transcript-turn" key={turn.id}>
        <div><strong>{turn.speaker_role === 'recruiter' ? 'Recruiter' : 'Candidate'}</strong> <time>{timestamp(turn.stream_start_ms)}–{timestamp(turn.stream_end_ms)}</time> <small title={turn.stream_id}>Stream {turn.stream_id.slice(0, 8)}</small></div>
        <p>{turn.text}</p>
        <details className="stage-transcript-sources"><summary>Source segments ({turn.segments.length})</summary>
          {turn.segments.map(segment => <div key={segment.id}>
            <small>{timestamp(segment.stream_start_ms)}–{timestamp(segment.stream_end_ms)} · ID {segment.id}</small>
            <p>{segment.text}</p>
          </div>)}
        </details>
      </article>)}
      {interim.map(turn => <article className="stage-transcript-turn stage-transcript-interim" key={`live-${turn.stream_id}`}>
        <div><strong>{turn.speaker_role === 'recruiter' ? 'Recruiter' : 'Candidate'}</strong><span className="stage-live-label">Live · updating</span></div>
        <p>{turn.text}</p>
      </article>)}
    </div>
    {!following && <button className="motion-jump-latest" onClick={() => {
      follow.current = true
      setFollowing(true)
      if (log.current) log.current.scrollTop = log.current.scrollHeight
    }}>Jump to latest</button>}
  </section>
}
