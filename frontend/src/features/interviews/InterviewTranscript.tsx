import { useEffect, useRef, useState } from 'react'
import { loadTranscript } from './sessionApi'
import type { TranscriptSnapshot } from './sessionApi'
import { groupTranscriptTurns } from './transcriptGrouping'
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
  const scope = `${jobId}:${applicationId}`
  const data = saved?.scope === scope ? saved.data : null
  const utterances = groupTranscriptTurns(data?.turns ?? [])

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const next = await loadTranscript(token, jobId, applicationId, controller.signal)
        if (!controller.signal.aborted) { setSaved({ scope, data: next }); setError('') }
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load transcript.')
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 2000)
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, applicationId, scope])

  const lastTurn = data?.turns.at(-1)?.id
  const captureLabels = { inactive: 'Capture inactive', connecting: 'Connecting transcription…',
    waiting_for_microphone: 'Waiting for microphone', listening: 'Transcribing', stopping: 'Saving final text…' }
  useEffect(() => {
    if (follow.current && log.current) log.current.scrollTop = log.current.scrollHeight
  }, [lastTurn])

  return <section className="stage-transcript" aria-label="Interview transcript">
    <header><strong>Interview transcript</strong><span className={`stage-badge${data?.capture.status === 'listening' ? '' : ' muted'}`}>{data ? captureLabels[data.capture.status] : 'Loading…'}</span></header>
    <p className="stage-transcript-info">Final segments are grouped for readability. Times are relative to each microphone stream; entries follow saved order.</p>
    {data?.transcript && <p className="stage-transcript-info">Version {data.transcript.version} · {data.transcript.status === 'capturing' ? 'Capture unfinished' : data.transcript.status.replaceAll('_', ' ')} · {utterances.length} utterances / {data.turns.length} segments · Refreshes every 2 seconds</p>}
    {data?.transcript?.error_code && <p className="stage-transcript-error" role="alert">Transcription stopped: {data.transcript.error_code.replaceAll('_', ' ').toLowerCase()}. Saved text is retained. If the interview is still active, rejoin to retry.</p>}
    {data?.transcript?.quality_flags.includes('FINALIZATION_PENDING') && <p className="stage-transcript-info">Capture stopped. Transcript timing and completeness still need review.</p>}
    {error && <p className="stage-transcript-error" role="alert">{error} {data ? 'Previously loaded text is retained; retrying.' : 'Retrying.'}</p>}
    {data?.has_earlier_turns && <p className="stage-transcript-info">Showing the latest 500 saved segments. Earlier segments remain stored.</p>}
    <div className="stage-transcript-turns" ref={log} onScroll={event => {
      const element = event.currentTarget
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48
    }}>
      {!data && !error && <p role="status">Loading transcript…</p>}
      {data && !data.turns.length && <div className="stage-empty"><strong>No saved transcript yet</strong><p>Transcription starts when the candidate is admitted. Speak with a microphone enabled; finalized text will appear here.</p></div>}
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
    </div>
  </section>
}
