import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../services/api'
import { listApplications } from '../cv/api'
import type { CandidateApplication } from '../cv/api'
import { AssessmentDetails } from '../cv/CVResults'
import { getJobDescription } from '../jd/api'
import type { JobDescriptionSummary, SavedJobDescription } from '../jd/api'
import { loadScreeningResults, loadShortlist, previewShortlist, saveShortlist } from './api'
import type { ExcludedCandidate, PreviewCandidate, SavedShortlist, ScreeningResults, ShortlistFilters, ShortlistPreview } from './api'
import '../cv/CVScreening.css'
import './Interviews.css'

const defaultFilters: ShortlistFilters = { mode: 'top_k', k: 5, minimum_score: null }
const exclusionLabels: Record<ExcludedCandidate['exclusion_reason'], string> = {
  outside_top_k: 'Outside top K · eligible for manual selection',
  below_minimum_score: 'Below minimum score · eligible for manual selection',
  outdated_assessment: 'Outdated assessment · screen again',
  assessment_not_completed: 'Assessment incomplete · review in CV Screening',
  current_ranking_unavailable: 'Current screening results could not be verified',
  assessment_missing: 'No assessment in the latest run · screen this application',
  score_missing: 'Screening score missing · screen again',
  score_invalid: 'Screening score invalid · screen again',
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    return 'Your session expired. Sign out and sign in again.'
  }
  return error instanceof Error ? error.message : 'Could not complete the request. Please retry.'
}

function eligibleCandidates(preview: ShortlistPreview) {
  return [...preview.proposed, ...preview.excluded.filter(candidate =>
    candidate.exclusion_reason === 'outside_top_k' || candidate.exclusion_reason === 'below_minimum_score',
  )]
}

export default function ShortlistWorkspace({ token, job }: { token: string; job: JobDescriptionSummary }) {
  const [applications, setApplications] = useState<CandidateApplication[]>([])
  const [description, setDescription] = useState<SavedJobDescription | null>(null)
  const [saved, setSaved] = useState<SavedShortlist | null>(null)
  const [preview, setPreview] = useState<ShortlistPreview | null>(null)
  const [screening, setScreening] = useState<ScreeningResults | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [mode, setMode] = useState<ShortlistFilters['mode']>('top_k')
  const [k, setK] = useState('5')
  const [minimumScore, setMinimumScore] = useState('80')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<'preview' | 'save' | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [reload, setReload] = useState(0)
  const [page, setPage] = useState(0)
  const requestLock = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    if (!loading && window.location.hash === '#interview-shortlist') document.getElementById('interview-shortlist')?.scrollIntoView({ block: 'start' })
  }, [loading])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    async function load() {
      try {
        const [candidates, stored, jd, results] = await Promise.all([
          listApplications(token, job.id, controller.signal),
          loadShortlist(token, job.id, controller.signal),
          getJobDescription(token, job.id, controller.signal),
          loadScreeningResults(token, job.id, controller.signal),
        ])
        const filters = stored?.filters ?? defaultFilters
        if (controller.signal.aborted) return
        setApplications(candidates)
        setSaved(stored)
        setDescription(jd)
        setScreening(results)
        setPreview(null)
        setSelected(stored?.members.map(member => member.application_id) ?? [])
        setMode(filters.mode)
        setK(String(filters.k ?? 5))
        setMinimumScore(filters.minimum_score ?? '80')
        setError('')
        setMessage(stored ? 'Saved selection restored. Generate a preview before saving changes.' : '')
      } catch (caught) {
        if (!controller.signal.aborted) setError(errorMessage(caught))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => { mounted.current = false; controller.abort() }
  }, [token, job.id, reload])

  const previewAssessments = new Map<string, PreviewCandidate | ExcludedCandidate>(
    [...(preview?.proposed ?? []), ...(preview?.excluded ?? [])].map(item => [item.application_id, item]),
  )
  const assessments = new Map(
    [...(screening?.ranked ?? []), ...(screening?.incomplete ?? [])].map(item => [item.application_id, item]),
  )
  const eligibleIds = new Set((screening?.ranked ?? []).map(item => item.application_id))
  const proposedIds = new Set(preview?.proposed.map(item => item.application_id) ?? [])
  const invalidSelection = selected.some(id => !eligibleIds.has(id))
  const savedIds = new Set(saved?.members.map(member => member.application_id) ?? [])
  const selectionChanged = selected.length !== savedIds.size || selected.some(id => !savedIds.has(id))
  const canSave = !!preview?.run && preview.run.status !== 'outdated'
    && !preview.warnings.length && !invalidSelection && !loading && !busy
  const ordered = [...applications].sort((a, b) =>
    (assessments.get(a.id)?.rank ?? Infinity) - (assessments.get(b.id)?.rank ?? Infinity)
    || a.id.localeCompare(b.id),
  )
  const currentPage = Math.min(page, Math.max(0, Math.ceil(ordered.length / 10) - 1))
  const visible = ordered.slice(currentPage * 10, currentPage * 10 + 10)
  const cutoff = preview?.proposed.at(-1)
  const cutoffTies = cutoff?.rank === undefined ? [] : (preview?.excluded ?? []).filter(
    item => eligibleIds.has(item.application_id) && item.rank === cutoff.rank,
  )

  function filtersChanged() {
    setPreview(null)
    setMessage('Filters changed. Generate a new preview before saving.')
    setError('')
  }

  async function generatePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestLock.current) return
    const needsK = mode !== 'minimum_score'
    const needsScore = mode !== 'top_k'
    if (needsK && (!/^\d+$/.test(k) || !Number.isSafeInteger(Number(k)) || Number(k) < 1)) {
      setError('Top K must be a positive whole number.')
      return
    }
    if (needsScore && (!minimumScore.trim() || !Number.isFinite(Number(minimumScore))
      || Number(minimumScore) < 0 || Number(minimumScore) > 100)) {
      setError('Minimum score must be between 0 and 100.')
      return
    }
    const filters: ShortlistFilters = {
      mode, k: needsK ? Number(k) : null, minimum_score: needsScore ? minimumScore : null,
    }
    requestLock.current = true
    setBusy('preview')
    setError('')
    setMessage('')
    try {
      const next = await previewShortlist(token, job.id, filters)
      if (!mounted.current) return
      setPreview(next)
      setScreening({
        run: next.run,
        ranked: eligibleCandidates(next),
        incomplete: next.excluded.filter(item =>
          item.exclusion_reason !== 'outside_top_k' && item.exclusion_reason !== 'below_minimum_score',
        ),
        warnings: next.warnings,
      })
      setSelected(next.proposed.map(item => item.application_id))
      setPage(0)
      setMessage(`${next.proposed_count} proposed. Adjust the checkboxes, then save your final selection.`)
    } catch (caught) {
      if (mounted.current) setError(errorMessage(caught))
    } finally {
      requestLock.current = false
      if (mounted.current) setBusy(null)
    }
  }

  async function saveSelection() {
    if (!preview || !canSave || requestLock.current) return
    requestLock.current = true
    setBusy('save')
    setError('')
    setMessage('')
    try {
      const next = await saveShortlist(token, preview, selected)
      if (!mounted.current) return
      setSaved(next)
      setSelected(next.members.map(member => member.application_id))
      setMessage(`Saved ${next.selected_count} interviewees for JD ${job.number}.`)
    } catch (caught) {
      if (mounted.current) {
        setError(errorMessage(caught))
        if (caught instanceof ApiError && caught.status === 409) setPreview(null)
      }
    } finally {
      requestLock.current = false
      if (mounted.current) setBusy(null)
    }
  }

  function toggleCandidate(id: string) {
    setSelected(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])
    setMessage('Selection changed. Save to keep your choices.')
  }

  return <div className="interview-workspace">
    <div className="interview-job-heading">
      <div><span className="interview-eyebrow">JD {job.number}</span><h2>{job.title}</h2></div>
      <button className="button-secondary" disabled={!!busy || loading} onClick={() => {
        setLoading(true); setPreview(null); setSelected([]); setMessage(''); setReload(value => value + 1)
      }}>Reload saved selection</button>
    </div>
    {error && <p className="interview-notice is-error" role="alert">{error}</p>}
    {loading ? <p className="interview-empty" role="status">Loading candidates and saved selection…</p> : <>
      <form className="interview-filter-panel" onSubmit={event => void generatePreview(event)}>
        <div><h3>Propose interviewees</h3><p>Use screening scores to start a shortlist, then make the final selection.</p></div>
        <div className="interview-filter-fields">
          <label>Filter method<select value={mode} disabled={!!busy} onChange={event => {
            setMode(event.target.value as ShortlistFilters['mode']); filtersChanged()
          }}><option value="top_k">Top K</option><option value="minimum_score">Minimum score</option><option value="both">Top K and minimum score</option></select></label>
          {mode !== 'minimum_score' && <label>Number of candidates<input type="number" min="1" step="1" required value={k} disabled={!!busy} onChange={event => { setK(event.target.value); filtersChanged() }} /></label>}
          {mode !== 'top_k' && <label>Minimum score (0–100)<input type="number" min="0" max="100" step="any" required value={minimumScore} disabled={!!busy} onChange={event => { setMinimumScore(event.target.value); filtersChanged() }} /></label>}
          <button className="button-primary" disabled={!!busy || !applications.length}>{busy === 'preview' ? 'Generating…' : 'Generate preview'}</button>
        </div>
      </form>
      {message && <p className="interview-notice" role="status">{message}</p>}
      {saved?.outdated && <p className="interview-notice is-warning">Your saved selection references older screening results. Generate a new preview, review the candidates, and save again.</p>}
      {invalidSelection && <p className="interview-notice is-warning">Some selected applications are no longer eligible. Uncheck them or generate a new preview before saving.</p>}
      {!!screening?.warnings.length && <p className="interview-notice is-warning">Current scores could not be verified. Check CV Screening and refresh before selecting interviewees.</p>}
      {screening && !screening.run && !!applications.length && <p className="interview-notice is-warning">No screening run exists for this job. Complete CV Screening to make candidates eligible.</p>}
      {!!cutoffTies.length && <p className="interview-notice is-warning">{cutoffTies.length} additional candidate(s) share the cutoff rank of {cutoff?.rank}. Top K keeps at most {preview?.filters.k} candidates; review the tied candidates below.</p>}
      {preview && !preview.proposed_count && !!applications.length && <p className="interview-hint">No candidates match the current rule. You can manually select other eligible candidates or adjust the filters.</p>}
      <section className="interview-saved-panel" aria-labelledby="saved-interviewees-heading">
        <div className="interview-saved-heading"><h3 id="saved-interviewees-heading">Saved interviewees</h3><span>{saved?.selected_count ?? 0} saved</span></div>
        {saved ? <>
          <p className="interview-hint">Saved for JD {job.number}. This list is restored when you return to this job.</p>
          {saved.members.length ? <ul className="interview-saved-list">{saved.members.map(member => <li key={member.application_id}>
            <div><strong>{member.name}</strong><span className="interview-email">{member.email}</span>{member.manually_added && <span className="interview-badge">Manually added</span>}{member.outdated && <span className="interview-badge">Needs updated screening</span>}</div>
            <div className="interview-saved-score"><strong>{Number(member.score.aggregate).toFixed(2)}%</strong><span>Saved rank {member.rank}</span></div>
          </li>)}</ul> : <p className="interview-hint">Your saved selection is empty.</p>}
        </> : <p className="interview-hint">No interviewees saved yet. Review candidates below and choose Save interviewees to keep your selection.</p>}
        {selectionChanged && <p className="interview-unsaved" role="status">Your current checkbox choices have unsaved changes.</p>}
      </section>
      <div className="interview-selection-bar">
        <div><strong>{selected.length} selected</strong><span>{applications.length} applications · {eligibleIds.size} eligible · {preview?.proposed_count ?? 0} proposed</span></div>
        <div className="interview-selection-actions">
          <button className="button-secondary" disabled={!!busy || !selected.length} onClick={() => { setSelected([]); setMessage('Selection cleared. Save to keep an empty selection.') }}>Clear selection</button>
          <button className="button-primary" disabled={!canSave} onClick={() => void saveSelection()}>{busy === 'save' ? 'Saving…' : 'Save interviewees'}</button>
        </div>
      </div>
      {!applications.length ? <div className="interview-empty"><h3>No candidates yet</h3><p>Import the applications for this job in CV Screening, then reload this selection.</p></div> : <div className="interview-table-scroll"><table className="interview-table">
        <thead><tr><th>Select</th><th>Candidate</th><th>Rank</th><th>Score</th><th>Review</th></tr></thead>
        <tbody>{visible.map(application => {
          const assessment = assessments.get(application.id)
          const previewAssessment = previewAssessments.get(application.id)
          const score = assessment?.score?.aggregate == null ? NaN : Number(assessment.score.aggregate)
          const isSelected = selected.includes(application.id)
          const eligible = eligibleIds.has(application.id)
          let reason = 'No current assessment · complete CV Screening'
          if (previewAssessment && 'exclusion_reason' in previewAssessment) {
            reason = exclusionLabels[previewAssessment.exclusion_reason]
          } else if (proposedIds.has(application.id)) {
            reason = 'Proposed by current filter'
          } else if (eligible) {
            reason = 'Current screening result · generate a preview to apply your filter'
          } else if (assessment?.outdated) {
            reason = 'Outdated assessment · screen again'
          } else if (assessment) {
            reason = screening?.warnings.length ? 'Current screening results could not be verified' : 'Assessment incomplete · review in CV Screening'
          }
          return <tr key={application.id} className={isSelected ? 'is-selected' : ''}>
            <td><input type="checkbox" aria-label={`Select ${application.full_name}`} checked={isSelected} disabled={!!busy || (!eligible && !isSelected)} onChange={() => toggleCandidate(application.id)} /></td>
            <td><strong>{application.full_name}</strong><span className="interview-email">{application.email}</span><span className={`interview-badge${proposedIds.has(application.id) ? ' is-proposed' : ''}`}>{proposedIds.has(application.id) ? 'Proposed' : eligible ? 'Eligible' : 'Needs review'}</span></td>
            <td>{assessment?.rank ?? '—'}</td><td>{Number.isFinite(score) && score >= 0 && score <= 100 ? `${score.toFixed(2)}%` : '—'}</td>
            <td><p className="interview-hint">{reason}</p>{assessment && <AssessmentDetails item={assessment} criteria={assessment.outdated ? null : description?.screening_profile ?? null} />}
              {application.cv_documents.map(document => <a className="interview-cv-link" key={document.id} href={`https://drive.google.com/file/d/${encodeURIComponent(document.drive_file_id)}/view`} target="_blank" rel="noopener noreferrer">View {document.original_filename ?? 'CV'} ↗</a>)}
            </td>
          </tr>
        })}</tbody>
      </table></div>}
      {ordered.length > 10 && <div className="interview-pagination"><span>Page {currentPage + 1} of {Math.ceil(ordered.length / 10)}</span><div><button className="button-secondary" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous</button><button className="button-secondary" disabled={(currentPage + 1) * 10 >= ordered.length} onClick={() => setPage(currentPage + 1)}>Next</button></div></div>}
      <div className="interview-footer">{!selectionChanged && !!saved?.members.length && !saved.outdated && !busy ? <Link to={`/recruiter/invitations?job=${encodeURIComponent(job.id)}`}>Invite saved shortlist →</Link> : <p className="interview-hint">Save a current shortlist before inviting candidates.</p>}</div>
    </>}
  </div>
}

