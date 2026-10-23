import { useEffect, useRef, useState } from 'react'
import JobEditor from './JobEditor'
import SuggestionChanges from './SuggestionChanges'
import type { JDConversation, SavedJobDescription } from './api'
import { actWindTunnel, applyWindTunnel, getWindTunnel, moreWindTunnel, previewWindTunnel } from './windTunnelApi'
import type { WindTunnelBatch, WindTunnelHistory, WindTunnelPreview, WindTunnelSuggestion } from './windTunnelApi'
import { batchDate, batchView } from './windTunnelView'
import './JobSuggestions.css'

function Limits() {
  return <footer className="suggestion-limits"><strong>About these suggestions:</strong> optional, based on synthetic examples. <details><summary>Read the limits</summary>
    <ul><li>Synthetic examples do not establish hiring accuracy, fairness or validity.</li><li>New batches assess each of three CVs once; repeatability is not measured.</li><li>Proposed text changes have not been tested with fresh matching.</li><li>Review both the description and screening criteria before applying. Applying does not publish the job.</li></ul>
  </details></footer>
}

export function UnapprovedSuggestions({ onNavigate }: { onNavigate: (section: 'description' | 'criteria') => void }) {
  return <section className="jd-suggestions" aria-label="Job suggestions"><header className="suggestion-heading"><div><h3>Suggestions</h3><p>Suggestions become available after this job is approved.</p></div><button className="button-secondary" disabled>More suggestions</button></header>
    <div className="suggestion-alert is-info"><span aria-hidden="true">i</span><p><strong>Approve this job first.</strong> After approval, Distill creates three synthetic CVs, checks them against your criteria and proposes up to three optional improvements. Nothing changes or publishes automatically.</p></div>
    <button className="button-secondary" onClick={() => onNavigate('criteria')}>Go to Screening criteria</button><Limits /></section>
}

type Props = { token: string; job: SavedJobDescription; onSaved: (updated: JDConversation) => void; onNavigate: (section: 'description' | 'criteria') => void; onReload: () => void }

export default function JobSuggestions({ token, job, onSaved, onNavigate, onReload }: Props) {
  const [history, setHistory] = useState<WindTunnelHistory | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [review, setReview] = useState<{ suggestion: WindTunnelSuggestion; version: string; preview: WindTunnelPreview; position: string } | null>(null)
  const operation = useRef<AbortController | null>(null)
  const moreRequest = useRef<string | null>(null)
  const reviewing = Boolean(review)
  const active = history?.batches.some(batch => ['queued', 'running'].includes(batch.status)) ?? false
  const locked = Boolean(job.editing_locked || history?.editing_locked)
  const latest = history?.batches[0]
  const older = history?.batches.slice(1) ?? []
  const pending = job.posting_status === 'sending' || job.posting_status === 'unknown'

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function refresh() {
      try {
        const next = await getWindTunnel(token, job.id, controller.signal)
        if (controller.signal.aborted) return
        setHistory(next)
        if (reviewing || next.batches.some(batch => ['queued', 'running'].includes(batch.status))) timer = setTimeout(() => { void refresh() }, 3000)
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load suggestions.')
      }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, job.id, job.edit_version, revision, reviewing])
  useEffect(() => () => operation.current?.abort(), [])

  async function perform(action: (signal: AbortSignal) => Promise<unknown>) {
    if (busy) return
    const controller = new AbortController()
    operation.current = controller
    setBusy(true); setError('')
    try {
      await action(controller.signal)
      if (!controller.signal.aborted) setRevision(value => value + 1)
    } catch (caught) {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not complete this action.')
    } finally {
      if (!controller.signal.aborted) { setBusy(false); operation.current = null }
    }
  }

  function requestMore() {
    void perform(async signal => {
      moreRequest.current ??= crypto.randomUUID()
      await moreWindTunnel(token, job.id, moreRequest.current, signal)
      moreRequest.current = null
    })
  }

  function showChanges(id: string) {
    const panel = document.getElementById(`suggestion-changes-${id}`) as HTMLDetailsElement | null
    if (panel) { panel.open = true; panel.scrollIntoView({ block: 'nearest' }) }
  }

  function card(suggestion: WindTunnelSuggestion, batch: WindTunnelBatch, index: number) {
    const stale = suggestion.status === 'stale' || (batch.stale && suggestion.status !== 'applied')
    const disabled = busy || locked || stale || history?.edit_version !== job.edit_version
    return <article className={`jd-suggestion-card${stale || locked && suggestion.status !== 'applied' ? ' is-unavailable' : ''}`} key={suggestion.id}>
      <header><h4>{suggestion.title}</h4>{suggestion.status === 'applied' ? <span className="suggestion-status is-success">✓ Applied</span> : stale ? <span className="suggestion-status is-warning">! Stale</span> : null}</header>
      <p>{suggestion.reason}</p><div className="suggestion-areas"><strong>Affects</strong>{suggestion.affected_areas.map(area => <span key={area}>{area === 'description' ? 'Description' : 'Screening criteria'}</span>)}</div>
      <details className="suggestion-exact-changes" id={`suggestion-changes-${suggestion.id}`}><summary><span className="when-closed">Show exact changes</span><span className="when-open">Hide exact changes</span></summary><SuggestionChanges patches={suggestion.patches} profile={job.screening_profile} /></details>
      <div className="suggestion-card-actions">{suggestion.status === 'applied' ? <button className="button-secondary" onClick={() => showChanges(suggestion.id)}>View applied change</button> : <><small>{stale ? 'Job changed' : locked ? 'Locked' : ''}</small><button className="button-primary" disabled={disabled} onClick={() => { void perform(async signal => {
        if (!history) return
        const preview = await previewWindTunnel(token, job.id, suggestion.id, history.edit_version, signal)
        if (!signal.aborted) setReview({ suggestion, version: history.edit_version, preview, position: `Reviewing suggestion ${index + 1} of ${batch.suggestions.length}` })
      }) }}>Review suggestion</button></>}</div>
    </article>
  }

  function batchContent(batch: WindTunnelBatch, historical = false) {
    const view = batchView(batch)
    const inProgress = ['queued', 'running'].includes(batch.status)
    const applied = batch.suggestions.filter(suggestion => suggestion.status === 'applied').length
    return <section className="jd-suggestion-batch" key={batch.id} aria-label={historical ? 'Previous batch' : 'Latest batch'}>
      <div className="suggestion-batch-meta"><strong>{historical ? 'Batch' : 'Latest batch'}</strong><time dateTime={batch.created_at}>{batchDate(batch.created_at)} · {batch.trigger === 'approval' ? 'after approval' : 'requested batch'}</time>
        <span className={`suggestion-status is-${view.tone}`}>{view.label === 'Ready' || view.label === 'Complete' || view.label === 'Applied' ? '✓ ' : view.tone === 'warning' ? '! ' : ''}{view.label}</span>
        {!inProgress && <small>{batch.suggestions.length} {batch.suggestions.length === 1 ? 'suggestion' : 'suggestions'}{applied > 0 ? ` · ${applied} applied` : ''}</small>}</div>
      <p className="suggestion-basis">Based on 3 synthetic CVs: all-rounder, keyword-stuffer, near-miss</p>
      {view.message && <div className={`suggestion-alert is-${view.tone}`} role={inProgress ? 'status' : view.tone === 'danger' ? 'alert' : undefined}><span aria-hidden="true">{view.tone === 'success' ? '✓' : view.tone === 'warning' ? '!' : view.tone === 'danger' ? '×' : '○'}</span><p><strong>{view.title}</strong> {view.message}</p></div>}
      {applied > 0 && <div className="suggestion-alert is-success" role="status"><span aria-hidden="true">✓</span><p><strong>Suggestion applied.</strong> The description and screening criteria were saved together. The job was not published and no new batch started.</p></div>}
      <div className="suggestion-state-actions">
        {inProgress && <button className="button-secondary" disabled={busy} onClick={() => { void perform(signal => actWindTunnel(token, job.id, batch.id, 'cancel', signal)) }}>Cancel batch</button>}
        {view.retry && !batch.stale && <button className="button-primary" disabled={busy || active || locked} onClick={() => { void perform(signal => actWindTunnel(token, job.id, batch.id, 'retry', signal)) }}>Retry</button>}
        {view.destination && <button className="button-secondary" onClick={() => onNavigate(view.destination!)}>{view.destination === 'criteria' ? 'Review screening criteria' : 'Go to Description'}</button>}
      </div>
      {batch.suggestions.map((suggestion, index) => card(suggestion, batch, index))}
    </section>
  }

  if (review) return <JobEditor token={token} target={{ conversationId: job.conversation_id, editVersion: review.version, content: job.editable_content ?? job.content, profile: job.screening_profile, approved: true, section: 'description' }}
    proposal={{ ...review.preview.edit, title: review.suggestion.title, patches: review.suggestion.patches, position: review.position,
      conflict: locked || history?.edit_version !== review.version,
      apply: signal => applyWindTunnel(token, job.id, review.suggestion.id, review.version, review.preview.review_token, signal) }}
    onCancel={() => { setReview(null); setRevision(value => value + 1); onReload() }}
    onSaved={updated => { setReview(null); setRevision(value => value + 1); onSaved(updated) }} />

  return <section className="jd-suggestions" aria-label="Job suggestions" aria-busy={busy}>
    <header className="suggestion-heading"><div><h3>Suggestions</h3><p>{!history ? 'Checking for suggestions on this job…' : 'Optional improvements to this approved job. Nothing changes until you review and apply a suggestion.'}</p></div>
      <div className="suggestion-more"><small>{active ? 'A batch is already in progress.' : locked ? pending ? 'Not available while posting is pending.' : 'Not available while the job is posted.' : ''}</small><button className="button-secondary" disabled={busy || active || locked || !history} onClick={requestMore}>More suggestions</button></div></header>
    {error && <div className="suggestion-alert is-danger" role="alert"><span aria-hidden="true">!</span><div><p>{error}</p><button className="button-secondary" disabled={busy} onClick={() => { setError(''); setRevision(value => value + 1); onReload() }}>Reload suggestions</button></div></div>}
    {!history && !error && <div className="suggestion-loading" role="status"><strong>Loading suggestions…</strong>{[0, 1].map(index => <div className="suggestion-skeleton" key={index} aria-hidden="true"><i /><i /><i /></div>)}</div>}
    {locked && <div className="suggestion-alert is-neutral"><span aria-hidden="true">▣</span><p><strong>{pending ? 'Posting pending.' : 'This job is posted.'}</strong> History stays readable. Reviewing and applying suggestions is turned off while this job is locked.</p></div>}
    {history && history.edit_version !== job.edit_version && <div className="suggestion-alert is-warning"><span aria-hidden="true">!</span><div><p>The job changed. Reload it before reviewing current suggestions.</p><button className="button-secondary" onClick={onReload}>Reload job</button></div></div>}
    {latest ? batchContent(latest) : history && <div className="suggestion-alert is-info"><span aria-hidden="true">i</span><p><strong>No batches yet.</strong> No suggestions have been generated for this job. Choose More suggestions to start.</p></div>}
    {older.length > 0 && <details className="suggestion-history"><summary>Previous batches <span>{older.length}</span></summary>{older.map(batch => batchContent(batch, true))}</details>}
    <Limits />
  </section>
}
