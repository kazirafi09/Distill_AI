import { useEffect, useState } from 'react'
import type { Run } from './CVResults'
import { screeningProgress } from './screeningProgressView'

export default function ScreeningProgress({ run, starting, syncing, lastChecked, refreshError }: {
  run?: Run | null; starting: boolean; syncing: boolean; lastChecked: string; refreshError: boolean
}) {
  const completionKey = run?.status === 'completed' ? `${run.id}:${run.updated_at ?? ''}` : ''
  const [dismissedCompletion, setDismissedCompletion] = useState('')
  useEffect(() => {
    if (!completionKey) return
    const timer = setTimeout(() => setDismissedCompletion(completionKey), 5000)
    return () => clearTimeout(timer)
  }, [completionKey])

  if (syncing) return <section className="screening-progress" aria-label="Application sync progress">
    <p role="status"><strong>Syncing applications…</strong></p>
    <progress aria-label="Syncing applications" />
    <p>Importing responses and downloading CV files. Counts will appear when the sync request finishes. Keep this page open until the outcome is confirmed.</p>
  </section>
  if (starting) return <section className="screening-progress" role="status"><strong>Starting screening…</strong><progress aria-label="Starting screening" /><p>Waiting for the server to confirm your request.</p></section>
  if (!run || (completionKey && dismissedCompletion === completionKey && !refreshError)) return null
  const progress = screeningProgress(run)
  return <section className={`screening-progress${progress.attention ? ' needs-attention' : ''}`} aria-label="Screening progress">
    <div role="status" aria-live="polite"><strong>{progress.title}</strong><p>{progress.finished} of {progress.total} processed · {progress.scored} scored · {progress.issues} processing issues</p></div>
    {progress.total > 0 ? <progress value={progress.finished} max={progress.total} aria-label="Applications processed" /> : progress.active ? <progress aria-label="Preparing screening" /> : null}
    <p>{progress.description}</p>
    <small>{refreshError ? 'Status refresh failed. Showing the last confirmed progress; retrying automatically.' : lastChecked ? `Status checked at ${new Date(lastChecked).toLocaleTimeString()}.` : 'Checking status…'}{run.updated_at && ` Run updated at ${new Date(run.updated_at).toLocaleTimeString()}.`}</small>
  </section>
}

