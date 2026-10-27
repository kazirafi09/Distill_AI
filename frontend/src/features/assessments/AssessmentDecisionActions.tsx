import { useEffect, useRef, useState } from 'react'
import { DrawerPanel } from './CandidateAssessment'
import { loadDecisionCandidates, saveFinalCandidates } from './api'
import type { DecisionCandidate, DecisionWorkspace } from './api'
import './AssessmentDecisionActions.css'

const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase())
const score = (value: unknown) => value == null ? '—' : `${Number(value).toFixed(1)} / 100`
function TextList({ values }: { values: string[] }) {
  return values.length ? <ul>{values.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p className="assessment-muted">None recorded.</p>
}

function Comparison({ candidates }: { candidates: DecisionCandidate[] }) {
  const [ids, setIds] = useState(['', ''])
  const entries = ids.map(id => candidates.find(item => item.application_id === id))
  const ready = entries.every(Boolean) && ids[0] !== ids[1]
  const pair = entries.filter((item): item is DecisionCandidate => !!item)
  const mismatch = ready && (pair[0].profile_version !== pair[1].profile_version || pair[0].rubric_version !== pair[1].rubric_version
    || pair[0].scoring_policy?.policy_sha256 !== pair[1].scoring_policy?.policy_sha256
    || JSON.stringify(pair[0].scores?.policy) !== JSON.stringify(pair[1].scores?.policy))
  const dimensions = [...new Set(pair.flatMap(item => Object.keys(item.performance)))]
  const qaDimensions = [...new Set(pair.flatMap(item => Object.keys(item.qa_scores)))]
  return <>
    <div className="decision-pickers">{ids.map((id, index) => <label key={index}>Candidate {index + 1}<select aria-label={`Comparison candidate ${index + 1}`} value={id} onChange={event => setIds(current => current.map((value, i) => i === index ? event.target.value : value))}>
      <option value="">Choose a candidate</option>{candidates.filter(item => item.comparable).map(item => <option key={item.application_id} value={item.application_id} disabled={ids[1 - index] === item.application_id}>{item.name}</option>)}
    </select></label>)}</div>
    {candidates.filter(item => item.comparable).length < 2 && <p className="assessment-warning">Two completed consolidated assessments are needed to compare candidates.</p>}
    {ready && <>
      {(mismatch || pair.some(item => item.outdated)) && <p className="assessment-warning">These assessments use different or older criteria or scoring settings. Review the scores separately.</p>}
      <p className="assessment-muted">Scores are provisional. “—” means unavailable, not zero.</p>
      <div className="decision-table-scroll"><table className="decision-comparison"><thead><tr><th>Assessment</th>{pair.map(item => <th key={item.application_id}>{item.name}</th>)}</tr></thead><tbody>
        {(['score_100', 'cv_score_100', 'interview_score_100'] as const).map((key, index) => <tr key={key}><th>{['Combined score', 'CV screening', 'Interview performance'][index]}</th>{pair.map(item => <td key={item.application_id}><strong>{score(item.scores?.[key])}</strong>{index === 0 && <p>{item.scores?.reason}</p>}</td>)}</tr>)}
        <tr><th>Assessment overview</th>{pair.map(item => <td key={item.application_id}><strong>{label(item.suitability.position ?? 'Unavailable')}</strong><p>{item.suitability.rationale}</p></td>)}</tr>
        {dimensions.map(dimension => <tr key={dimension}><th>{label(dimension)}</th>{pair.map(item => <td key={item.application_id}><strong>{score(item.performance[dimension]?.score)}</strong><p>{item.performance[dimension]?.justification}</p></td>)}</tr>)}
        {qaDimensions.map(dimension => <tr key={`qa-${dimension}`}><th>Q&amp;A · {label(dimension)}</th>{pair.map(item => <td key={item.application_id}>{score(item.qa_scores[dimension])}</td>)}</tr>)}
        {(['strengths', 'weaknesses', 'evidence_gaps', 'recommended_areas'] as const).map(key => <tr key={key}><th>{key === 'evidence_gaps' ? 'Assessment gaps' : key === 'recommended_areas' ? 'Follow-up areas' : label(key)}</th>{pair.map(item => <td key={item.application_id}><TextList values={item.findings[key]} /></td>)}</tr>)}
        <tr><th>CV claim review</th>{pair.map(item => <td key={item.application_id}>{item.claim_review.length ? item.claim_review.map((claim, index) => <section key={index}><strong>{label(claim.status)} · {claim.claim}</strong><p>{claim.explanation}</p>{claim.uncertainty && <p>{claim.uncertainty}</p>}<p>{claim.follow_up}</p>{claim.review_action && <p>Recruiter review: {label(claim.review_action)}. {claim.review_note}</p>}</section>) : <p>{label(item.feature_statuses.cv_claim_review ?? 'Unavailable')}</p>}</td>)}</tr>
        <tr><th>Interview quality</th>{pair.map(item => <td key={item.application_id}><p>{item.quality_summary ?? label(item.feature_statuses.interview_quality ?? 'Unavailable')}</p>{item.quality_findings.map((finding, index) => <section key={index}><strong>{label(finding.category)}</strong><p>{finding.observation}</p>{finding.uncertainty && <p>{finding.uncertainty}</p>}<p>{finding.improvement}</p></section>)}</td>)}</tr>
      </tbody></table></div>
    </>}
  </>
}

function DecisionModal({ token, jobId, jobTitle, mode, close }: { token: string; jobId: string; jobTitle: string; mode: 'compare' | 'finalize'; close: () => void }) {
  const [data, setData] = useState<DecisionWorkspace | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [revision, setRevision] = useState(0)
  const mounted = useRef(true)
  const saving = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    void loadDecisionCandidates(token, jobId, controller.signal).then(next => {
      if (!controller.signal.aborted) { setData(next); setSelected(next.selection.map(item => item.application_id)); setConfirmed(false); setError('') }
    }).catch(caught => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load candidate scores.') })
    return () => controller.abort()
  }, [token, jobId, revision])
  async function save() {
    if (!data || !confirmed || saving.current) return
    saving.current = true; setBusy(true); setError('')
    try {
      const next = await saveFinalCandidates(token, jobId, data, selected)
      if (mounted.current) { setData(next); setConfirmed(false); setMessage('Final candidates saved.') }
    } catch (caught) { if (mounted.current) { setError(caught instanceof Error ? caught.message : 'Could not save the final selection.'); setConfirmed(false) } }
    finally { saving.current = false; if (mounted.current) setBusy(false) }
  }
  const invalid = selected.some(id => !data?.candidates.some(item => item.application_id === id && item.eligible))
  return <DrawerPanel title={mode === 'compare' ? 'Compare candidates' : 'Finalize candidates'} modal className="assessment-decisions" close={() => { if (!saving.current) close() }}>
    <p className="decision-intro">{jobTitle} · Main scores and findings for recruiter review.</p>
    {error && <div role="alert" className="assessment-error"><p>{error}</p><button className="button-secondary" disabled={busy} onClick={() => { setData(null); setRevision(value => value + 1); setMessage('') }}>Reload candidates</button></div>}
    {message && <p role="status" className="decision-success">{message}</p>}
    {!data && !error && <p role="status">Loading candidate assessments…</p>}
    {data && (mode === 'compare' ? <Comparison candidates={data.candidates} /> : <>
      <section className="decision-saved"><h3>Saved final candidates</h3><p>{data.selection.length ? data.selection.map(item => item.name).join(', ') : 'No final candidates saved yet.'}</p>{data.updated_at && <small>Saved {new Date(data.updated_at).toLocaleString()}</small>}</section>
      {data.selection_outdated && <p className="assessment-warning">Scores or candidates changed since the last selection. Review and confirm the selection again.</p>}
      <p className="decision-intro">Choose the candidates to select for this job. Completed interviews and current consolidated and interview scores are required. No emails or offers are sent.</p>
      {!data.candidates.length && <p>No candidates are available for this job.</p>}
      <div className="decision-table-scroll"><table className="decision-selection"><thead><tr><th>Select</th><th>Candidate</th><th>CV</th><th>Interview</th><th>Combined</th><th>Status</th></tr></thead><tbody>{data.candidates.map(item => <tr key={item.application_id}>
        <td><input type="checkbox" aria-label={`Select ${item.name} for final selection`} checked={selected.includes(item.application_id)} disabled={busy || (!item.eligible && !selected.includes(item.application_id))} onChange={event => { setSelected(current => event.target.checked ? [...current, item.application_id] : current.filter(id => id !== item.application_id)); setConfirmed(false); setMessage('') }} /></td>
        <th>{item.name}</th><td>{score(item.scores?.cv_score_100)}</td><td>{score(item.scores?.interview_score_100)}</td><td><strong>{score(item.scores?.score_100)}</strong></td><td>{item.blocker ?? 'Ready for recruiter selection'}{item.scores?.warnings?.map(warning => <p key={warning}>{warning}</p>)}</td>
      </tr>)}</tbody></table></div>
      {selected.filter(id => !data.candidates.some(item => item.application_id === id)).map(id => <p key={id}>A previously selected candidate is no longer available. <button className="button-secondary" onClick={() => { setSelected(current => current.filter(value => value !== id)); setConfirmed(false) }}>Remove unavailable candidate</button></p>)}
      <footer className="decision-confirm"><strong>{selected.length} candidates selected</strong><label><input type="checkbox" checked={confirmed} disabled={busy || invalid} onChange={event => setConfirmed(event.target.checked)} />I confirm this final candidate selection for the job.</label>
        <button className="button-primary" disabled={busy || !confirmed || invalid || !!error} onClick={() => void save()}>{busy ? 'Saving…' : 'Confirm final candidates'}</button>
      </footer>
    </>)}
  </DrawerPanel>
}

export default function AssessmentDecisionActions(props: { token: string; jobId: string; jobTitle: string }) {
  const [mode, setMode] = useState<'compare' | 'finalize' | null>(null)
  return <><button className="button-secondary" onClick={() => setMode('compare')}>Compare candidates</button><button className="button-primary" onClick={() => setMode('finalize')}>Finalize candidates</button>
    {mode && <DecisionModal {...props} mode={mode} close={() => setMode(null)} />}</>
}
