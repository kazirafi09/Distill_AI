import { useEffect, useRef, useState } from 'react'
import { apiRequest } from '../../services/api'
import { ActionButton, Notice, SectionTabs, StatusBadge } from '../../components/recruitment/RecruitmentUI'
import type { Run } from './api'
import type { InterviewPolicy, PerformanceDimension } from './InterviewScoringSettings'
import './PostInterviewAssessment.css'

type Kind = 'cv_claim_review' | 'interview_performance' | 'interview_quality'
type Quote = {turn_id: string; quote: string}
type Claim = {id: string; claim: string; status: string; explanation: string; scope_comparison: string; follow_up: string; cv_evidence: {quote: string; source_id: string}; interview_evidence: Quote[]}
type Dimension = {score: number | null; reason: string; justification: string; basis: string; evidence: Quote[]; reference_evidence: {reference_id: string; quote: string; filename?: string}[]}
type Finding = {category: string; observation: string; improvement: string; group_ids: string[]; evidence: Quote[]}
type Review = {id: string; finding_id: string; action: string; note: string; created_at: string}
type PolicySnapshot = {revision: number; policy_sha256: string; policy: InterviewPolicy}
type Artifact = {artifact_sha256: string; claims?: Claim[]; selection_note?: string; dimensions?: Record<PerformanceDimension, Dimension>;
  summary?: {overall_score_100: string | null; status: string; assessed_dimensions: number; assessed_weight: number; missing_weight: number}; scoring_settings?: PolicySnapshot;
  findings?: Finding[]; process_summary?: string; coverage?: {criterion_id: string; text: string; status: string; group_ids: string[]}[];
  speaking_time?: {status: string; reason?: string; candidate_ms?: number; recruiter_ms?: number; note?: string}; warnings: string[]}
type Item = {run: Omit<Run, 'kind'> & {kind: Kind}; result: Artifact | null; reviews: Review[]}
type Workspace = {items: Item[]; blockers: Record<Kind, string | null>; scoring_settings: PolicySnapshot; review_required: boolean}
const names: Record<Kind, string> = {cv_claim_review: 'CV claim review', interview_performance: 'Interview performance', interview_quality: 'Interview quality'}
const claimLabels: Record<string, string> = {supported_in_interview:'Supported in interview',potential_contradiction:'Potential contradiction',discussed_not_substantiated:'Discussed, not substantiated',not_discussed:'Not discussed',unable_to_assess:'Unable to assess'}
const label = (text: string) => text.replaceAll('_',' ').replace(/^./, value => value.toUpperCase())

function Evidence({quotes, onEvidence}: {quotes: Quote[]; onEvidence: (id: string) => void}) {
  return <>{quotes.map((quote, index) => <blockquote key={`${quote.turn_id}-${index}`}><p>“{quote.quote}”</p><button type="button" onClick={() => onEvidence(quote.turn_id)}>View in source transcript</button></blockquote>)}</>
}

function ClaimReview({item, token, onRefresh, onEvidence}: {item: Item; token: string; onRefresh: () => void; onEvidence: (id: string) => void}) {
  const [notes, setNotes] = useState<Record<string,string>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  async function review(id: string, action: 'reviewed' | 'clarification') {
    if(lock.current) return
    lock.current = true; setBusy(id); setError('')
    try {
      await apiRequest(`/assessments/runs/${item.run.id}/findings/${id}/reviews`, {token, method:'POST',body:{action,note:notes[id] ?? ''}})
      setNotes(current => ({...current,[id]:''})); onRefresh()
    } catch(caught) {setError(caught instanceof Error ? caught.message : 'Could not save review.')}
    finally {lock.current = false; setBusy('')}
  }
  return <><p className="assessment-muted">{item.result?.selection_note}</p>
    <Notice title="Prompts for human follow-up" tone="info">Interview support remains self-report. Potential contradictions need contextual review and never trigger an automatic rejection.</Notice>
    {error && <p role="alert" className="assessment-error">{error}</p>}
    {item.result?.claims?.map(claim => <article className="post-interview-finding" key={claim.id}>
      <StatusBadge tone={claim.status === 'supported_in_interview' ? 'success' : claim.status === 'potential_contradiction' ? 'warning' : 'neutral'}>{claimLabels[claim.status] ?? label(claim.status)}</StatusBadge>
      <h4>{claim.claim}</h4><p>{claim.explanation}</p><p className="assessment-muted">Context: {claim.scope_comparison}</p>
      <details><summary>CV and interview evidence</summary><blockquote><small>CV · {claim.cv_evidence.source_id}</small><p>“{claim.cv_evidence.quote}”</p></blockquote>
        <Evidence quotes={claim.interview_evidence} onEvidence={onEvidence} />{!claim.interview_evidence.length && <p className="assessment-muted">No supporting quote asserted. The complete saved transcript was supplied for review; missing discussion is not a failed claim.</p>}</details>
      <p><strong>Follow up:</strong> {claim.follow_up}</p>
      <details><summary>Recruiter review · {item.reviews.filter(review => review.finding_id === claim.id).length} saved</summary>
        {item.reviews.filter(review => review.finding_id === claim.id).map(review => <div className="post-interview-review" key={review.id}><strong>{label(review.action)}</strong><small>{new Date(review.created_at).toLocaleString()}</small><p>{review.note || 'Reviewed without clarification.'}</p></div>)}
        <label className="post-interview-note">Clarification<textarea rows={3} maxLength={2000} value={notes[claim.id] ?? ''} disabled={!!busy} onChange={event => setNotes({...notes,[claim.id]:event.target.value})} /></label>
        <div className="post-interview-actions"><ActionButton disabled={!!busy} onClick={() => void review(claim.id,'reviewed')}>{busy === claim.id ? 'Saving…' : 'Mark reviewed'}</ActionButton>
          <ActionButton disabled={!!busy || !notes[claim.id]?.trim()} onClick={() => void review(claim.id,'clarification')}>Add clarification</ActionButton></div>
      </details>
    </article>)}
    {item.result?.claims?.length === 0 && <p>No substantive role-relevant CV claims were selected for review.</p>}
  </>
}

export default function PostInterviewAssessment({token, parentId, onEvidence}: {token: string; parentId: string; onEvidence: (id: string) => void}) {
  const [data, setData] = useState<Workspace | null>(null)
  const [tab, setTab] = useState('interview_performance')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [revision, setRevision] = useState(0)
  const [selectedRuns, setSelectedRuns] = useState<Partial<Record<Kind,string>>>({})
  const lock = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {mounted.current = true; return () => {mounted.current = false}},[])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const result = await apiRequest<Workspace>(`/assessments/runs/${parentId}/post-interview`,{token,signal:controller.signal,cache:'no-store'})
        if(controller.signal.aborted) return
        setData(result); timer = setTimeout(() => void poll(),result.items.some(item => ['queued','running'].includes(item.run.status)) ? 3000 : 15000)
      } catch(caught) {if(!controller.signal.aborted) {setError(caught instanceof Error ? caught.message : 'Could not load post-interview analysis.'); timer = setTimeout(() => void poll(),15000)}}
    }
    void poll(); return () => {controller.abort(); clearTimeout(timer)}
  },[token,parentId,revision])
  async function start(kind: Kind, retry?: string) {
    if(lock.current) return
    lock.current = true; setBusy(true); setError('')
    try {
      if(retry) await apiRequest(`/assessments/runs/${retry}/retry`,{token,method:'POST'})
      else {
        const result = await apiRequest<{blockers: Record<string,string>}>(`/assessments/runs/${parentId}/post-interview`,{token,method:'POST',body:{features:[kind],confirm_f5_review:reviewed}})
        if(result.blockers[kind]) throw new Error(result.blockers[kind])
      }
      if(mounted.current) {setSelectedRuns(current => ({...current,[kind]:undefined})); setRevision(value => value + 1)}
    } catch(caught) {if(mounted.current) setError(caught instanceof Error ? caught.message : 'Could not start analysis.')}
    finally {lock.current = false; if(mounted.current) setBusy(false)}
  }
  const kind = tab as Kind
  const item = data?.items.find(value => value.run.kind === kind && value.run.id === selectedRuns[kind]) ?? data?.items.find(value => value.run.kind === kind)
  const result = item?.result
  const older = data?.items.filter(value => value.run.kind === kind && value.run.id !== item?.run.id) ?? []
  return <section className="post-interview-assessment">
    <div className="recruitment-section-heading"><div><h2>Post-interview insights</h2><p>Evidence for recruiter review, with interview performance scored separately from the CV.</p></div></div>
    <SectionTabs tabs={Object.entries(names).map(([id,name]) => ({id,label:name}))} active={tab} onChange={setTab} />
    {error && <p role="alert" className="assessment-error">{error}</p>}
    {!data && !error && <p role="status">Loading saved insights…</p>}
    {data && <>
      {data.items.some(value => value.run.kind === kind) && <label className="post-interview-version">Saved analysis<select value={item?.run.id ?? ''} onChange={event => setSelectedRuns({...selectedRuns,[kind]:event.target.value})}>{data.items.filter(value => value.run.kind === kind).map(value => <option key={value.run.id} value={value.run.id}>{new Date(value.run.created_at).toLocaleString()} · {label(value.run.status)} · {value.run.id.slice(0,8)}</option>)}</select></label>}
      {data.review_required && <label className="post-interview-confirm"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />I reviewed the saved F5 questions, scores and evidence.</label>}
      <div className="post-interview-actions"><ActionButton variant="primary" disabled={busy || !!data.blockers[kind] || (data.review_required && !reviewed) || (!!item && ['queued','running'].includes(item.run.status))} onClick={() => void start(kind)}>{busy ? 'Saving…' : item ? `Analyze ${names[kind].toLowerCase()} with current settings` : `Start ${names[kind].toLowerCase()}`}</ActionButton>
        {item && <StatusBadge tone={item.run.status === 'completed' ? 'success' : item.run.status === 'failed' || item.run.status === 'needs_review' ? 'warning' : 'info'}>{label(item.run.status)}</StatusBadge>}</div>
      {data.blockers[kind] && <p className="assessment-warning">{data.blockers[kind]}</p>}
      <p className="assessment-muted">Identical sources and settings reuse the saved run. A changed scoring revision creates a new performance assessment.</p>
      {item?.run.error_code && <Notice title="Analysis needs attention" tone="warning">{label(item.run.error_code)}. Saved source evidence is retained.{item.run.can_retry && <ActionButton disabled={busy} onClick={() => void start(kind,item.run.id)}>Retry saved analysis</ActionButton>}</Notice>}
      {item && ['queued','running'].includes(item.run.status) && <p role="status">{label(item.run.stage)} · attempt {item.run.attempt}/{item.run.max_attempts}. Other assessment sections remain available.</p>}
      {kind === 'cv_claim_review' && item && result && <ClaimReview key={item.run.id} item={item} token={token} onRefresh={() => setRevision(value => value + 1)} onEvidence={onEvidence} />}
      {kind === 'interview_performance' && result?.dimensions && result.summary && <>
        <div className="post-interview-total"><h3>Interview performance</h3><strong>{result.summary.overall_score_100 === null ? 'Overall score unavailable — insufficient interview evidence' : `${result.summary.overall_score_100} / 100`}</strong><StatusBadge tone={result.summary.status === 'assessed' ? 'success' : 'warning'}>{label(result.summary.status)}</StatusBadge>
          <p>{result.summary.assessed_dimensions} assessed weighted dimensions · {result.summary.assessed_weight}% assessed weight · {result.summary.missing_weight}% missing weight.</p></div>
        {result.scoring_settings && <p className="assessment-muted">Scoring revision {result.scoring_settings.revision} · minimum {result.scoring_settings.policy.minimum_assessed_dimensions} dimensions · required: {result.scoring_settings.policy.essential_dimensions.map(label).join(', ') || 'None'}.
          {result.scoring_settings.policy_sha256 !== data.scoring_settings.policy_sha256 && ' Job settings have changed. Run a new analysis to use them; this score retains its original rubric.'}</p>}
        <div className="post-interview-dimensions">{Object.entries(result.dimensions).map(([name, dimension]) => <article className="assessment-score" key={name}><h3>{label(name)}</h3><strong>{dimension.score === null ? 'Not assessed' : `${dimension.score * 25} / 100`}</strong><p>{result.scoring_settings?.policy.weights[name as PerformanceDimension]}% weight · {label(dimension.basis)}</p>
          <p>{dimension.justification}</p><details><summary>Supporting evidence</summary><Evidence quotes={dimension.evidence} onEvidence={onEvidence} />{dimension.reference_evidence.map((reference,index) => <blockquote key={`${reference.reference_id}-${index}`}><small>{reference.filename ?? reference.reference_id}</small><p>“{reference.quote}”</p></blockquote>)}</details></article>)}</div>
      </>}
      {kind === 'interview_quality' && result && <>
        <h3>Interview process quality</h3><p>{result.process_summary}</p>
        {result.coverage && <section className="assessment-coverage"><h3>Competency coverage</h3><p>Explored means discussed; it does not establish proficiency.</p>{result.coverage.map(coverage => <div key={coverage.criterion_id}><span>{coverage.text}</span><strong>{label(coverage.status)} · {coverage.group_ids.length} groups</strong></div>)}</section>}
        {result.findings?.map((finding,index) => <article className="post-interview-finding" key={index}><h4>{label(finding.category)}</h4><p>{finding.observation}</p><p><strong>Improve:</strong> {finding.improvement}</p><small>Question groups: {finding.group_ids.join(', ')}</small><Evidence quotes={finding.evidence} onEvidence={onEvidence} /></article>)}
        {result.speaking_time && <Notice title="Speaking time">{result.speaking_time.status === 'measured' ? <><p>Candidate: {((result.speaking_time.candidate_ms ?? 0)/60000).toFixed(1)} minutes · interviewer: {((result.speaking_time.recruiter_ms ?? 0)/60000).toFixed(1)} minutes.</p><p>{result.speaking_time.note}</p></> : result.speaking_time.reason}</Notice>}
      </>}
      {result && <p className="assessment-muted">{result.warnings.join(' ')} Saved {new Date(item!.run.created_at).toLocaleString()} · {item!.run.id}</p>}
      {!!older.length && <details><summary>Earlier {names[kind].toLowerCase()} runs · {older.length}</summary>{older.map(previous => <p className="assessment-muted" key={previous.run.id}>{new Date(previous.run.created_at).toLocaleString()} · {label(previous.run.status)} · {previous.run.id}{previous.result?.summary?.overall_score_100 != null ? ` · ${previous.result.summary.overall_score_100}/100` : ''}</p>)}</details>}
    </>}
  </section>
}
