import { errorLabels } from './screeningMessages'
import { useEffect, useRef, useState } from 'react'
import { apiRequest } from '../../services/api'

export type Run = { id: string; status: string; total: number; finished: number; completed: number; error_code: string | null; profile_version: number; rubric_version: string; updated_at?: string }
export type Evidence = { quote: string; source_id: string; file_version_id: string; ocr_derived: boolean }
type Match = { requirement_id: string; decision: string; justification: string; unsupported_requirement_parts?: string[]; evidence: Evidence[] }
export type Score = { aggregate: string; evidence_coverage: string; must_have_review_flags: string[];
  dimensions: { dimension: string; score: string; approved_percentage: string; aggregate_contribution: string; justifications: { requirement_id: string; justification: string }[] }[];
  contributions: { requirement_id: string; priority: string; weight: string; credit: string; weighted_credit: string }[] }
export type Assessment = { id: string; application_id: string; name: string; rank?: number; status: string; outdated: boolean; score: Score | null; matches: Match[]; warnings: string[]; error_code: string | null }
export type Results = { run: Run | null; profile: { criteria: { id: string; text: string; priority: string | null }[] } | null;
  ranked: Assessment[]; incomplete: Assessment[]; history: Run[]; warnings: string[] }

const label = (value: string) => value.replaceAll('_', ' ').toLowerCase()
const assessmentWarnings = (warnings: string[]) => warnings.filter(w => !w.startsWith('CV_SHEET_') && w !== 'CV_CONTACT_FIELDS_UNKNOWN')
const percent = (value: string) => `${Number(value).toFixed(2)}%`
export function AssessmentDetails({ item, criteria }: { item: Assessment; criteria: Results['profile'] }) {
  const aggregate = item.score?.aggregate == null ? NaN : Number(item.score.aggregate)
  const score = Number.isFinite(aggregate) && aggregate >= 0 && aggregate <= 100 ? item.score : null
  return <details className="cv-screening-details"><summary>View breakdown, matches and evidence</summary>
    {score && <>
      <p>Evidence coverage (criteria with checked excerpts): {percent(String(Number(score.evidence_coverage) * 100))}. Missing documented support earns zero credit and is not proof of inability.</p>
      {!!(score.must_have_review_flags ?? []).length && <p className="cv-review-flag">Recruiter review: {(score.must_have_review_flags ?? []).length} Must-have criteria are not fully met. No automatic rejection.</p>}
      <table><thead><tr><th>Dimension</th><th>Score</th><th>Approved weight</th><th>Overall contribution</th></tr></thead>
        <tbody>{(score.dimensions ?? []).map(d => <tr key={d.dimension}><td>{label(d.dimension)}</td><td>{percent(d.score)}</td><td>{percent(d.approved_percentage)}</td><td>{Number(d.aggregate_contribution).toFixed(2)}</td></tr>)}</tbody></table>
    </>}
    {item.matches.map(match => {
      const criterion = criteria?.criteria.find(c => c.id === match.requirement_id)
      const contribution = score?.contributions?.find(c => c.requirement_id === match.requirement_id)
      return <article className="cv-criterion-result" key={match.requirement_id}>
        <h4>{criterion?.text ?? match.requirement_id}</h4>
        <p>{criterion?.priority} · <strong>{label(match.decision)}</strong>{contribution && ` · weight ${contribution.weight}, credit ${contribution.credit}, weighted credit ${contribution.weighted_credit}`}</p>
        <p>{match.justification}</p>
        {!!match.unsupported_requirement_parts?.length && <p>Unsupported requested components: {match.unsupported_requirement_parts.join('; ')}</p>}
        {!match.evidence.length && <p>No supporting excerpt established.</p>}
        {match.evidence.map((e, i) => <blockquote key={i}><p>{e.quote}</p><small>{label(e.source_id)} · file version {e.file_version_id}{e.ocr_derived ? ' · OCR-derived' : ''}</small></blockquote>)}
      </article>
    })}
    {!!assessmentWarnings(item.warnings).length && <ul>{assessmentWarnings(item.warnings).map(w => <li key={w}>{errorLabels[w] ?? label(w)}</li>)}</ul>}
  </details>
}

export default function CVResults({ token, jobId }: { token: string; jobId: string }) {
  const [results, setResults] = useState<Results | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const lock = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await apiRequest<Results>(`/cv/screening/jobs/${jobId}/results`, { token, signal: controller.signal })
        if (!controller.signal.aborted) { setResults(next); setError(''); timer = setTimeout(() => void poll(), next.run && ['queued', 'running'].includes(next.run.status) ? 3000 : 10000) }
      } catch (caught) {
        if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load screening results.'); timer = setTimeout(() => void poll(), 10000) }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, refresh])

  async function action(resume: boolean) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try {
      await apiRequest<Run>(resume && results?.run ? `/cv/screening/runs/${results.run.id}/resume` : `/cv/screening/jobs/${jobId}/start`, { method: 'POST', token })
      setRefresh(v => v + 1)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Screening could not start.') }
    finally { lock.current = false; setBusy(false) }
  }
  const run = results?.run
  const active = !!run && ['queued', 'running'].includes(run.status)
  return <section className="cv-screening-results" aria-label="Screening results">
    <h3>Screening and scores</h3>
    <div className="cv-screening-actions"><button className="button-primary" disabled={!results || busy || active} onClick={() => void action(!!run && ['failed', 'completed_with_issues'].includes(run.status))}>{busy ? 'Saving request…' : active ? 'Screening in progress…' : 'Screening'}</button>
    </div>
    {error && <p role="alert" className="cv-error">{error}</p>}
    {run && <p role="status">{label(run.status)} · {run.finished}/{run.total} processed · {run.completed} completed · profile {run.profile_version ?? 'missing'} · rubric {run.rubric_version}</p>}
    {run?.error_code && <p className="cv-error">{errorLabels[run.error_code] ?? label(run.error_code)}</p>}
    {results?.warnings.map(w => <p key={w} className="cv-review-flag">{label(w)}</p>)}
    {!!results?.ranked.length && <div className="cv-table-scroll"><table className="cv-ranked-table"><thead><tr><th>Rank</th><th>Candidate</th><th>Overall score</th><th>Review and details</th></tr></thead>
      <tbody>{results.ranked.map(item => <tr key={item.id}><td>{item.rank}</td><td>{item.name}</td><td>{percent(item.score!.aggregate)}</td><td><AssessmentDetails item={item} criteria={results.profile} /></td></tr>)}</tbody></table></div>}
    {!!results?.incomplete.length && <div><h4>Incomplete or outdated assessments</h4>{results.incomplete.map(item => <article className="cv-incomplete-result" key={item.id}>
      <strong>{item.name}</strong><p>{item.outdated ? 'Outdated — start screening with current inputs.' : label(item.status)}</p>
      {item.error_code && <p>{errorLabels[item.error_code] ?? label(item.error_code)}</p>}
      {!!assessmentWarnings(item.warnings).length && !item.matches.length && <ul>{assessmentWarnings(item.warnings).map(w => <li key={w}>{label(w)}</li>)}</ul>}
      {!!item.matches.length && <AssessmentDetails item={item} criteria={results.profile} />}
    </article>)}</div>}
    {!!results?.history.length && <details><summary>Previous runs</summary>{results.history.map(old => <p key={old.id}>{label(old.status)} · profile {old.profile_version} · rubric {old.rubric_version} · {old.completed}/{old.total} completed</p>)}</details>}
  </section>
}
