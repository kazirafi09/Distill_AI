import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import RecruitmentHeader from '../interviews/RecruitmentHeader'
import { useRecruitmentJob } from '../interviews/useRecruitmentJob'
import { loadResult, loadWorkspace, retryF5, rerunF5, recoverF5, startF5, startF6 } from './api'
import CandidateAssessment from './CandidateAssessment'
import InterviewScoringSettings from './InterviewScoringSettings'
import CandidateWorkspace from './CandidateWorkspace'
import AssessmentDecisionActions from './AssessmentDecisionActions'
import { DemoBanner } from '../../components/recruitment/RecruitmentUI'
import type { Result, Run, Workspace } from './api'
import { stageLabel } from './format'
import '../interviews/InterviewLobby.css'
import './Assessments.css'

const activeRun = (run: Run) => ['queued', 'running'].includes(run.status)
function RunDetails({ token, jobId, jobTitle, jobNumber, runs, runId, onBack, onRetry, onRecover, onRerun, onConsolidate, onSelect, busy }: {
  token: string; jobId: string; jobTitle: string; jobNumber: number; runs: Run[]; runId: string; busy: boolean
  onBack: () => void; onRetry: () => void; onRecover: () => void; onRerun: () => void; onConsolidate: (confirm: boolean) => void; onSelect: (id: string) => void
}) {
  const [data, setData] = useState<Result | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await loadResult(token, runId, controller.signal)
        if (controller.signal.aborted) return
        if (result.run.job_id !== jobId) { setError('This run belongs to another job. Select a run from this list.'); return }
        setData(result); setError('')
        timer = setTimeout(() => void poll(), activeRun(result.run) ? 3000 : 15000)
      } catch (caught) {
        if (!controller.signal.aborted) { setError(caught instanceof Error ? caught.message : 'Could not load this assessment.'); timer = setTimeout(() => void poll(), 10000) }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [token, jobId, runId])
  if (!data) return <div className="assessment-empty" role={error ? 'alert' : 'status'}>{error || 'Loading assessment…'}<button type="button" className="button-secondary" onClick={onBack}>Back to assessments</button></div>
  return <>{error && <p role="alert" className="assessment-error">{error}</p>}<CandidateAssessment data={data} token={token} jobTitle={jobTitle} jobNumber={jobNumber} runs={runs} busy={busy} onBack={onBack} onSelect={onSelect} onRetry={onRetry} onRecover={onRecover} onRerun={onRerun} onConsolidate={onConsolidate} /></>
}

function AssessmentWorkspace({ token, jobId, jobTitle, jobNumber }: { token: string; jobId: string; jobTitle: string; jobNumber: number }) {
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
  async function act(retry = false, recover = false, applicationId?: string, rerun = false) {
    if (lock.current || ((retry || rerun) && !runId)) return
    lock.current = true; setBusy(true); setError('')
    try {
      const run = rerun ? await rerunF5(token, runId!) : recover ? await recoverF5(token, runId!) : retry ? await retryF5(token, runId!) : await startF5(token, jobId, applicationId)
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
    {!runId && <InterviewScoringSettings token={token} jobId={jobId} />}
    <CandidateWorkspace hidden={!!runId} token={token} jobTitle={jobTitle} data={data} error={error} jobId={jobId} busy={busy} onRetry={() => setRevision(value => value + 1)} onOpen={selectRun} onStart={id => void act(false, false, id)} onPage={next => { setData(null); setOffset(next) }} />
    {runId && <>
      {error && <p className="assessment-error" role="alert">{error}</p>}
      <RunDetails key={`${runId}:${revision}`} token={token} jobId={jobId} jobTitle={jobTitle} jobNumber={jobNumber} runs={data?.runs ?? []} runId={runId} busy={busy} onBack={() => setParams(current => { current.delete('run'); return current })} onConsolidate={confirm => void consolidate(confirm)} onSelect={selectRun} onRerun={() => { if (!busy) void act(false, false, undefined, true) }} onRecover={() => { if (!busy) void act(true, true) }} onRetry={() => { if (!busy) void act(true) }} />
    </>}
    {data && !runId && <details className="assessment-tools"><summary>Sample analysis and saved run history</summary>
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
    <RecruitmentHeader title="Assessments" description="Pick a job, then open a candidate’s assessment." jobs={picker.jobs} job={picker.job} onSelect={id => { picker.selectJob(id) }} actions={picker.job && <AssessmentDecisionActions key={picker.job.id} token={token} jobId={picker.job.id} jobTitle={picker.job.title} />} />
    {picker.loading && <p role="status">Loading jobs…</p>}
    {picker.error && <div role="alert"><p>{picker.error}</p><button className="button-secondary" onClick={picker.retry}>Try again</button></div>}
    {!picker.loading && !picker.error && !picker.job && <p>No jobs yet. <Link to="/recruiter/job-descriptions">Create a job description</Link> to get started.</p>}
    {!picker.loading && !picker.error && picker.job && <AssessmentWorkspace key={picker.job.id} token={token} jobId={picker.job.id} jobTitle={picker.job.title} jobNumber={picker.job.number} />}
  </section>
}
