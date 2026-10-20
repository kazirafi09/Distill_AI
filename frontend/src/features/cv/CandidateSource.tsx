import { useEffect, useRef, useState } from 'react'
import { getCVProfile, parseCV } from './api'
import type { CandidateApplication, ParseResult } from './api'
import type { Assessment, Run } from './CVResults'
import { warningLabel } from './parsingLabels'
import { candidateFileLabel } from './profileView'
import { errorLabels } from './screeningMessages'
import { validScore } from './screeningView'
import './CandidateSource.css'

const dimensionLabels: Record<string, string> = {
  technical_skills: 'Technical skills', work_experience: 'Relevant experience',
  qualifications: 'Education and qualifications', additional_criteria: 'Additional requirements',
}
function number(value: unknown): number | null {
  if (value == null || String(value).trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 100 ? parsed : null
}
function label(value: unknown) {
  const parsed = number(value)
  return parsed === null ? '—' : parsed.toLocaleString('en-GB', { maximumFractionDigits: 1 })
}

export function ScoreDetails({ item, run, onRequirements }: { item?: Assessment; run?: Run | null; onRequirements: () => void }) {
  const score = validScore(item)
  if (!item?.score || score === null) return <div className="ui-state"><h3>No score available yet</h3><p>{item?.error_code ? errorLabels[item.error_code] ?? warningLabel(item.error_code) : 'Screen this application to calculate its match score.'}</p></div>
  const coverage = number(item.score.evidence_coverage)
  return <div className="candidate-score-details">
    <section className="score-overview"><div><h3>Overall match score</h3><p>How closely the CV’s documented evidence matches this job’s criteria.</p>{run?.profile_version != null && <span className="ui-badge is-info">Criteria version {run.profile_version}</span>}</div><strong>{label(score)}<small> / 100</small></strong></section>
    <section className="score-calculation"><h3>How each category contributes</h3><p className="muted">Each category has a share of the overall score. Its match score determines how many points it adds.</p>
      <div className="score-category-list">{item.score.dimensions.map(dimension => <article className="score-category" key={dimension.dimension}><header><h4>{dimensionLabels[dimension.dimension] ?? dimension.dimension.replaceAll('_', ' ')}</h4><strong>{label(dimension.score)}<small> / 100 match</small></strong></header>{number(dimension.score) !== null && <progress max="100" value={number(dimension.score)!} aria-label={`${dimensionLabels[dimension.dimension] ?? dimension.dimension} match`} />}<footer><span>{label(dimension.approved_percentage)}% of the overall score</span><strong>Adds {label(dimension.aggregate_contribution)} points</strong></footer></article>)}</div>
      <div className="score-total"><span>Overall match score</span><strong>{label(score)} / 100</strong></div>
    </section>
    <section className="score-evidence-note"><h3>Evidence behind this score</h3><p>{coverage !== null && coverage <= 1 ? `${label(coverage * 100)}% of criteria have a checked CV excerpt. ` : ''}Missing evidence earns no documented credit; it does not prove that a candidate lacks the ability.</p><button className="text-link" onClick={onRequirements}>Review requirement evidence →</button></section>
    <p className="muted">This score supports your review. It is not a hiring recommendation, and no candidate is rejected automatically.</p>
  </div>
}

type CVFile = CandidateApplication['cv_documents'][number]

function FileStatus({ token, file, onProfile }: { token: string; file: CVFile; onProfile: () => void }) {
  const [result, setResult] = useState<ParseResult | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)
  const lock = useRef(false)
  useEffect(() => {
    if (!file.file_version_id) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const next = await getCVProfile(token, file.file_version_id!, controller.signal)
        if (!controller.signal.aborted) {
          setResult(next); setError('')
          if (!['completed', 'needs_review', 'failed', 'not_started'].includes(next.status)) timer = setTimeout(() => void load(), 2500)
        }
      } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not check this file.') }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, file.file_version_id, revision])
  async function retry() {
    if (lock.current || !file.file_version_id) return
    lock.current = true; setBusy(true); setError('')
    try { setResult(await parseCV(token, file.file_version_id)); setRevision(value => value + 1) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The CV could not be read. Try again.') }
    finally { lock.current = false; setBusy(false) }
  }
  const available = file.download_status === 'downloaded'
  const complete = result?.status === 'completed'
  const canRetry = available && !!file.file_version_id && ['failed', 'needs_review', 'not_started'].includes(result?.status ?? '')
  const reading = !!result && !['failed', 'needs_review', 'not_started', 'completed'].includes(result.status)
  const warnings = [...new Set(result?.warnings ?? [])]
  const status = !available || !file.file_version_id ? 'CV download needed' : error ? 'Could not check CV status' : busy || reading ? 'Reading CV…' : complete ? 'CV read successfully' : result?.status === 'failed' ? 'CV could not be read' : result?.status === 'needs_review' ? 'Some CV content needs review' : result?.status === 'not_started' ? 'CV has not been read yet' : 'Checking CV status…'
  return <div className="cv-file-status">
    <p className={`cv-file-status-label${complete ? ' is-ready' : ''}`} role="status"><span aria-hidden="true">{complete ? '✓' : '•'}</span>{status}</p>
    {result?.error_code && <p className="ui-error">{warningLabel(result.error_code)}</p>}
    {(!available || !file.file_version_id) && <p className="muted">Use “Sync applications” to download this CV from the connected source.</p>}
    {error && <div role="alert"><p className="ui-error">{error}</p><button className="button-secondary" onClick={() => setRevision(value => value + 1)}>Refresh status</button></div>}
    <div className="cv-file-secondary-actions">{result?.profile && <button className="text-link" onClick={onProfile}>View candidate profile →</button>}{canRetry && <button className="button-secondary" disabled={busy} onClick={() => void retry()}>{busy ? 'Reading CV…' : result?.status === 'not_started' ? 'Read CV' : 'Retry reading CV'}</button>}</div>
    {canRetry && <p className="muted">After the CV is read successfully, screen the application again to update its score.</p>}
    {warnings.length > 0 && <details className="profile-review-notice"><summary>{warnings.length} {warnings.length === 1 ? 'detail' : 'details'} to check</summary><ul>{warnings.map(warning => <li key={warning}>{warningLabel(warning)}</li>)}</ul></details>}
    <details className="cv-file-metadata"><summary>File details</summary><dl><div><dt>Original filename in Google Drive</dt><dd>{file.original_filename ?? 'Not provided'}</dd></div><div><dt>File download</dt><dd>{available ? 'Available' : file.download_status === 'failed' ? 'Failed — sync applications to retry' : 'Not downloaded yet'}</dd></div>{result && <div><dt>Text reading method</dt><dd>{result.ocr_used ? 'Read from scanned images' : result.status === 'not_started' ? 'Not read yet' : result.status === 'completed' ? 'Read directly from the document' : 'Not confirmed yet'}</dd></div>}</dl></details>
  </div>
}

export function CandidateFiles({ token, application, onProfile }: { token: string; application: CandidateApplication; onProfile: (fileId: string) => void }) {
  return <div className="candidate-files"><div className="candidate-files-intro"><h3>CV files</h3><p className="muted">Files linked to this application. Open the original document or check whether it was read successfully.</p></div>{!application.cv_documents.length && <div className="ui-state">No CV is linked to this application. Sync applications to import its files.</div>}{application.cv_documents.map((file, index) => <article className="candidate-file-card" key={file.id}><header><div className="cv-file-symbol" aria-hidden="true">CV</div><h4>{candidateFileLabel(application.full_name, file.original_filename, index, application.cv_documents.length)}</h4><a className="button-secondary" href={`https://drive.google.com/file/d/${encodeURIComponent(file.drive_file_id)}/view`} target="_blank" rel="noopener noreferrer">Open file ↗</a></header><FileStatus key={file.file_version_id ?? file.id} token={token} file={file} onProfile={() => onProfile(file.id)} /></article>)}</div>
}
