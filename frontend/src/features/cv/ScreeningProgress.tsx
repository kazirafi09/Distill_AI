import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import type { Run } from './CVResults'
import { screeningProgress } from './screeningProgressView'

function StepIcon({ step }: { step: string }) {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {step === 'read' ? <><path d="M14 3H6a1 1 0 0 0-1 1v16h14V8Z" /><path d="M14 3v5h5M8 12h8M8 16h5" /></> : step === 'match' ? <><path d="m4 7 2 2 3-4M12 7h8m-16 9 2 2 3-4M12 16h8" /></> : <><path d="M5 20V10h3v10M11 20V4h3v16M17 20v-7h3v7" /></>}
  </svg>
}

export default function ScreeningProgress({ run, starting, lastChecked, refreshError }: {
  run?: Run | null; starting: boolean; lastChecked: string; refreshError: boolean
}) {
  const completionKey = run?.status === 'completed' ? `${run.id}:${run.updated_at ?? ''}` : ''
  const [dismissedCompletion, setDismissedCompletion] = useState('')
  useEffect(() => {
    if (!completionKey) return
    const timer = setTimeout(() => setDismissedCompletion(completionKey), 5000)
    return () => clearTimeout(timer)
  }, [completionKey])

  if (starting) return <section className="screening-progress is-starting" role="status"><div className="screening-progress-heading"><div className="screening-progress-title"><span className="screening-progress-mark is-active" aria-hidden="true" /><strong>Preparing CV reading and screening</strong></div><span className="screening-progress-badge">Starting</span></div><progress aria-label="Starting screening" /><ol className="screening-start-stages" aria-label="Processing steps"><li>1. Read CVs</li><li>2. Match criteria</li><li>3. Score candidates</li></ol></section>
  if (!run || (completionKey && dismissedCompletion === completionKey && !refreshError)) return null
  const progress = screeningProgress(run)
  return <section className={`screening-progress${progress.attention ? ' needs-attention' : ''}`} aria-label="Screening progress">
    <div className="screening-progress-heading"><div className="screening-progress-title"><span className={`screening-progress-mark${progress.active ? ' is-active' : ''}`} aria-hidden="true">{progress.active ? '' : progress.attention ? '!' : '✓'}</span><div><strong>{progress.title}</strong><p>{progress.finished} of {progress.total} applications processed · {progress.scored} scored{progress.issues > 0 && ` · ${progress.issues} need attention`}</p></div></div><span className="screening-progress-badge">{progress.percentage !== null && (progress.completedUnits > 0 || !progress.active) ? `${progress.percentage}% processed` : run.status === 'queued' ? 'Queued' : 'In progress'}</span></div>
    {(progress.total > 0 || progress.active) && <progress value={progress.active && progress.completedUnits === 0 ? undefined : progress.percentage ?? undefined} max="100" aria-label="Overall screening progress" />}
    <ol className="screening-progress-stages" aria-label="Processing steps">{progress.stages.map(stage => <li key={stage.id} className={`screening-stage is-${stage.state}${stage.done && stage.state === 'waiting' ? ' is-partial' : ''}`} aria-current={stage.state === 'active' ? 'step' : undefined} title={stage.detail}
      style={{ '--step-progress': `${stage.total ? Math.min(100, ((stage.done ?? 0) + stage.skipped) / stage.total * 100) : 0}%` } as CSSProperties}>
      <span className="screening-stage-icon" aria-hidden="true">{stage.state === 'done' ? '✓' : stage.state === 'issue' ? '!' : <StepIcon step={stage.id} />}</span>
      <strong>{stage.label}</strong>
      <small>{stage.done === undefined ? 'Waiting' : `${stage.done} / ${stage.total ?? '—'} ${stage.unit}`}{stage.skipped > 0 && ` · ${stage.skipped} unavailable`}</small>
    </li>)}</ol>
    <footer className="screening-progress-footer"><p>{refreshError ? 'Status refresh failed. Progress may be out of date; retrying automatically.' : progress.active ? 'You can leave this page. Processing continues in the background.' : progress.description}{progress.skippedUnits > 0 && ' Progress includes checks stopped by processing issues.'}</p><small>{lastChecked && `Updated ${new Date(lastChecked).toLocaleTimeString()}`}</small></footer>
  </section>
}

