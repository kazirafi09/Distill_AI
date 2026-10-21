import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import RecruitmentHeader from '../interviews/RecruitmentHeader'
import { useRecruitmentJob } from '../interviews/useRecruitmentJob'
import { dimensions, loadResult, loadWorkspace, retryF5, recoverF5, startF5, startF6 } from './api'
import ConsolidatedAssessment from './ConsolidatedAssessment'
import FullReport from './FullReport'
import InterviewScoringSettings from './InterviewScoringSettings'
import PostInterviewAssessment from './PostInterviewAssessment'
import CandidateWorkspace from './CandidateWorkspace'
import { ActionButton, DemoBanner } from '../../components/recruitment/RecruitmentUI'
import type { Answer, Result, Run, Turn, Workspace } from './api'
import { errorLabel, scoreLabel, stageLabel, timeLabel } from './format'
import '../interviews/InterviewLobby.css'
import './Assessments.css'

const activeRun = (run: Run) => ['queued', 'running'].includes(run.status)
const titleCase = (text: string) => text.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase())
const retrievalLabels: Record<string, string> = {
  retrieved: 'Relevant uploaded passages were supplied for accuracy review. Citations below identify passages actually used.',
  partial: 'Some reference sources could not be searched. Accuracy review received only the available passages.',
  failed: 'Reference retrieval was unavailable. No retrieved evidence was supplied for accuracy review.',
  no_ready_sources: 'No ready supporting documents were available when this analysis started.',
  no_relevant_matches: 'No sufficiently relevant reference passages were found for this question.',
}

function AnswerCard({ answer, turns, criteria, onEvidence }: {
  answer: Answer; turns: Turn[]; criteria: Result['criteria']; onEvidence: (id: string) => void
}) {
  const group = answer.group
  const contextIds = new Set([...group.question_turn_ids, ...group.follow_up_turn_ids, ...group.clarification_turn_ids, ...group.revisit_turn_ids, ...group.answer_turn_ids])
  return <article className="assessment-answer">
    <div className="assessment-answer-heading"><div><span className="assessment-kicker">{group.id} · {titleCase(group.answer_state)}</span><h3>{group.topic}</h3></div></div>
    <div className="assessment-group-scores">{dimensions.map(dimension => <div key={dimension}><span>{titleCase(dimension)}</span><strong>{scoreLabel(answer.evaluations[dimension].score_100)}</strong></div>)}</div>
    <details className="assessment-conversation"><summary>Question, answer and follow-ups</summary>
      {turns.filter(turn => contextIds.has(turn.id)).map(turn => <p key={turn.id}><strong>{turn.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'}:</strong> {turn.text}</p>)}
    </details>
    <details><summary>Score explanations and evidence</summary>
      {answer.reference_retrieval && retrievalLabels[answer.reference_retrieval.status] &&
        <p className="assessment-muted">{retrievalLabels[answer.reference_retrieval.status]}</p>}
      {dimensions.map(dimension => {
        const evaluation = answer.evaluations[dimension]
        return <section className="assessment-explanation" key={dimension}>
          <h4>{titleCase(dimension)} <span>{scoreLabel(evaluation.score_100)}</span></h4><p>{evaluation.justification}</p>
          {evaluation.score === null && <p className="assessment-muted">{titleCase(evaluation.reason)}</p>}
          {evaluation.accuracy_warning && <p className="assessment-warning">Accuracy includes unverified model knowledge.</p>}
          {evaluation.evidence.map((evidence, index) => <blockquote key={`${evidence.turn_id}-${index}`}><p>{evidence.quote}</p>
            <button type="button" onClick={() => onEvidence(evidence.turn_id)}>View transcript · {timeLabel(evidence.session_start_ms)}</button></blockquote>)}
          {!!evaluation.reference_evidence.length && <details><summary>Technical reference notes</summary>{evaluation.reference_evidence.map((reference, index) => <blockquote key={index}><p>{reference.quote}</p>
            {reference.filename
              ? <span>{reference.filename}{reference.location ? ` · ${reference.location}` : ''}</span>
              : <a href={reference.url} target="_blank" rel="noreferrer">{reference.reference_id}</a>}
            <small> · source version {reference.version}</small></blockquote>)}</details>}
        </section>
      })}
    </details>
    {!!group.criterion_ids.length && <p className="assessment-mapped">Explores: {group.criterion_ids.map(id => criteria.find(item => item.id === id)?.text ?? id).join(' · ')}</p>}
  </article>
}

function RunDetails({ token, jobId, runId, onRetry, onRecover, onConsolidate, onSelect, busy }: { token: string; jobId: string; runId: string; onRetry: () => void; onRecover: () => void; onConsolidate: (confirm: boolean) => void; onSelect: (id: string) => void; busy: boolean }) {
  const [data, setData] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [highlight, setHighlight] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const transcript = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await loadResult(token, runId, controller.signal)
        if (controller.signal.aborted) return
        if (result.run.job_id !== jobId) { setError('This run belongs to another job. Select a run from this list.'); return }
        setData(result); setError('')
        if (activeRun(result.run)) timer = setTimeout(() => void poll(), 3000)
      } catch (caught) {
        if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load this assessment.'); timer = setTimeout(() => void poll(), 10000) }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, runId])

  function showEvidence(id: string) {
    setHighlight(id)
    if (transcript.current) transcript.current.open = true
    requestAnimationFrame(() => { const row = document.getElementById(`assessment-turn-${id}`); row?.scrollIntoView({ behavior: 'smooth', block: 'center' }); row?.focus({ preventScroll: true }) })
  }
  if (!data) return <div className="assessment-empty" role={error ? 'alert' : 'status'}>{error || 'Loading assessment…'}</div>
  const run = data.run
  return <div className="assessment-detail">
    {error && <p role="alert" className="assessment-error">{error}</p>}
    <header className="assessment-detail-heading"><div><p className="assessment-kicker">{run.kind === 'f6' ? 'F6 · Consolidated assessment' : 'F5 · Interview analysis'}</p><h2>{run.label}</h2><p className="assessment-muted">{new Date(run.created_at).toLocaleString()} · Criteria version {run.profile_version}</p></div>
      <span className={`assessment-status is-${run.status}`}>{stageLabel(run.stage)}</span></header>
    <FullReport key={run.id} token={token} runId={run.id} parentId={run.kind === 'f6' ? run.parent_run_id! : run.id} completed={run.status === 'completed'} />
    {run.recovered_from && <p className="assessment-warning">This recovery reuses validated answers from the previous run and retries the failed evaluation. Earlier scores have not been recalculated. <button className="button-secondary" onClick={() => onSelect(run.recovered_from!)}>View original run</button></p>}
    <p className="assessment-warning">{run.synthetic ? 'Synthetic sample — this is not a real candidate assessment. ' : ''}AI scores and conclusions are provisional and need review. CV and interview scores remain separate.</p>
    {run.kind === 'f5' && run.status === 'completed' && <section className="f6-start"><h3>Consolidated assessment</h3>
      {data.f6?.ready ? <>{data.f6.review_required && <label><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} /> I have reviewed the F5 scores and source evidence.</label>}
        <button className="button-primary" disabled={busy || (data.f6.review_required && !reviewed)} onClick={() => onConsolidate(reviewed)}>{busy ? 'Saving…' : run.synthetic ? 'Consolidate synthetic sample' : 'Generate consolidated assessment'}</button>
        <p className="assessment-muted">Combines the exact saved CV assessment and this F5 result. Existing scores are preserved.</p></>
        : <p className="assessment-muted">{data.f6?.blocker ?? 'Consolidation input is not ready.'}</p>}</section>}
    {data.outdated && <p className="assessment-warning">Job criteria have changed. These results use the saved criteria from this run.</p>}
    {activeRun(run) && <div className="assessment-progress" role="status"><strong>{stageLabel(run.stage)}</strong><p>{run.completed_groups} of {run.total_groups || '…'} {run.kind === 'f6' ? 'analysis branches saved' : 'Q&A groups saved'} · execution attempt {run.attempt}/{run.max_attempts}</p>
      {run.total_groups > 0 && <progress value={run.completed_groups} max={run.total_groups} aria-label="Saved answer groups" />}
      {run.stage === 'retry_wait' && <p>Automatic retry scheduled for {new Date(run.next_attempt_at).toLocaleTimeString()}.</p>}</div>}
    {run.error_code && <div className="assessment-error" role="alert"><p>{errorLabel(run.error_code, data.result?.validation_error)}</p>{run.can_retry && <button className="button-secondary" onClick={onRetry}>Retry from saved work</button>}{run.can_recover && <button className="button-secondary" disabled={busy} onClick={onRecover}>{busy ? 'Saving…' : 'Recover failed evaluation'}</button>}</div>}
    {run.kind === 'f6' && data.result?.findings && data.result.source_registry && <ConsolidatedAssessment data={data} onEvidence={showEvidence} onParent={onSelect} />}
    {run.status === 'completed' && <PostInterviewAssessment key={run.parent_run_id ?? run.id} token={token} parentId={run.kind === 'f6' ? run.parent_run_id! : run.id} onEvidence={showEvidence} />}
    {run.kind === 'f6' && <h2 className="f6-interview-heading">Saved interview scores</h2>}
    <div className="assessment-score-grid">{dimensions.map(dimension => {
      const summary = data.dimension_summaries[dimension]
      return <section className="assessment-score" key={dimension}><h3>{titleCase(dimension)}</h3><strong>{scoreLabel(summary.score_100)}</strong><p>{summary.assessed_count} assessed · {summary.not_assessable_count} not assessed</p></section>
    })}</div>
    <p className="assessment-muted">{activeRun(run) ? 'Scores summarize saved groups so far. ' : ''}Each dimension averages its assessed groups. Unknown scores are excluded; zero scores are included.</p>
    <section className="assessment-answers"><h2>Questions and answers <span>{data.answers.length}</span></h2>
      {!data.answers.length && <p className="assessment-muted">Validated answers will appear here as processing completes.</p>}
      {data.answers.map(answer => <AnswerCard key={answer.group.id} answer={answer} turns={data.source_turns} criteria={data.criteria} onEvidence={showEvidence} />)}
    </section>
    {!!data.result?.coverage?.length && <section className="assessment-coverage"><h2>Job requirement coverage</h2><p className="assessment-muted">Explored means discussed; it does not establish proficiency or verified experience.</p>
      {data.result.coverage.map(item => <div key={item.criterion_id}><span>{data.criteria.find(criterion => criterion.id === item.criterion_id)?.text ?? item.criterion_id}</span><strong>{item.exploration === 'unasked' ? 'Not asked' : `Explored · ${item.group_ids.length} groups`}</strong></div>)}</section>}
    <details ref={transcript} className="assessment-source"><summary>Source transcript · {data.source_turns.length} segments</summary>
      <p className="assessment-muted">{data.source_turns.some(turn => turn.session_start_ms == null) ? 'Saved text is final. Shared timestamps and cross-speaker chronology are unverified; audio gaps may omit speech. Uncertain answers are not assessed.' : run.synthetic ? 'Times come from the synthetic fixture timeline.' : 'Times come from the finalized interview timeline.'}</p>
      {data.source_turns.map(turn => <div id={`assessment-turn-${turn.id}`} tabIndex={-1} key={turn.id} className={`assessment-source-turn ${highlight === turn.id ? 'is-highlighted' : ''}`}>
        <small>{turn.speaker_role === 'candidate' ? 'Candidate' : 'Interviewer'} · {timeLabel(turn.session_start_ms)}</small><p>{turn.text}</p></div>)}
    </details>
  </div>
}

function AssessmentWorkspace({ token, jobId }: { token: string; jobId: string }) {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<Workspace | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [offset, setOffset] = useState(0)
  const lock = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await loadWorkspace(token, jobId, offset, controller.signal)
        if (controller.signal.aborted) return
        setData(result); setError(''); timer = setTimeout(() => void poll(), result.runs.some(activeRun) ? 3000 : 10000)
      } catch (caught) {
        if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load assessments.'); timer = setTimeout(() => void poll(), 10000) }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, revision, offset])
  const runId = params.get('run') ?? undefined
  function selectRun(id: string) { setParams(current => { current.set('run', id); return current }) }
  async function act(retry = false, recover = false, applicationId?: string) {
    if (lock.current || (retry && !runId)) return
    lock.current = true; setBusy(true); setError('')
    try {
      const run = recover ? await recoverF5(token, runId!) : retry ? await retryF5(token, runId!) : await startF5(token, jobId, applicationId)
      if (mounted.current) { selectRun(run.id); setRevision(value => value + 1) }
    } catch (caught) { if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not save the request.') }
    finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  async function consolidate(confirm: boolean) {
    if (lock.current || !runId) return
    lock.current = true; setBusy(true); setError('')
    try {
      const run = await startF6(token, runId, confirm)
      if (mounted.current) { selectRun(run.id); setRevision(value => value + 1) }
    } catch (caught) { if (mounted.current) setError(caught instanceof Error ? caught.message : 'Could not start consolidation.') }
    finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  return <>
    <InterviewScoringSettings token={token} jobId={jobId} />
    <CandidateWorkspace hidden={!!runId} data={data} error={error} jobId={jobId} busy={busy} onRetry={() => setRevision(value => value + 1)} onOpen={selectRun} onStart={id => void act(false, false, id)} onPage={next => { setData(null); setOffset(next) }} />
    {runId && <><div className="assessment-back"><ActionButton onClick={() => setParams(current => { current.delete('run'); return current })}>← All assessments</ActionButton><Link to={`/recruiter/interviews?job=${jobId}`}>Open interviews</Link></div>
      {error && <p className="assessment-error" role="alert">{error}</p>}
      <RunDetails key={`${runId}:${revision}`} token={token} jobId={jobId} runId={runId} busy={busy} onConsolidate={confirm => void consolidate(confirm)} onSelect={selectRun} onRecover={() => { if (!busy) void act(true, true) }} onRetry={() => { if (!busy) void act(true) }} />
    </>}
    {data && <details className="assessment-tools"><summary>Sample analysis and saved run history</summary>
    {data.synthetic_demo_available && <section className="assessment-controls"><div><h2>Analyze a synthetic sample</h2><p>Use an invented interview to explore the analysis workflow.</p></div>
      <button className="button-secondary" disabled={busy || !data.profile_ready} onClick={() => void act()}>{busy ? 'Saving…' : 'Analyze sample'}</button>
      <DemoBanner>Invented interview content. It is stored separately from candidate assessments. Existing identical runs are reused.</DemoBanner>
      {!data.profile_ready && <p className="assessment-warning">Save this job’s screening criteria before starting analysis.</p>}
    </section>}
    <aside className="assessment-run-list assessment-history"><h2>Assessment history</h2>{!data.runs.length && <p className="assessment-muted">No assessments yet. Start analysis from the candidate list.</p>}
      {data.runs.map(run => <button key={run.id} className={runId === run.id ? 'is-selected' : ''} onClick={() => selectRun(run.id)}><strong>{run.label}</strong><span>{run.kind.toUpperCase()} · {run.synthetic ? 'Synthetic sample' : 'Interview'} · {stageLabel(run.stage)}</span><small>{new Date(run.created_at).toLocaleDateString()} · {run.completed_groups}/{run.total_groups || '…'} {run.kind === 'f6' ? 'branches' : 'groups'}</small></button>)}
      {data.has_more_runs && <p className="assessment-muted">Showing the latest 50 runs.</p>}</aside>
    </details>}
  </>
}

export default function Assessments({ token }: { token: string }) {
  const picker = useRecruitmentJob(token)
  return <section className="recruitment-page assessments-page">
    <RecruitmentHeader title="Assessments" description="Pick a job, then open a candidate’s assessment." jobs={picker.jobs} job={picker.job} onSelect={id => { picker.selectJob(id) }} />
    {picker.loading && <p role="status">Loading jobs…</p>}
    {picker.error && <div role="alert"><p>{picker.error}</p><button className="button-secondary" onClick={picker.retry}>Try again</button></div>}
    {!picker.loading && !picker.error && !picker.job && <p>No jobs yet. <Link to="/recruiter/job-descriptions">Create a job description</Link> to get started.</p>}
    {!picker.loading && !picker.error && picker.job && <AssessmentWorkspace key={picker.job.id} token={token} jobId={picker.job.id} />}
  </section>
}
