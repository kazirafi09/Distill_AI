import { Fragment, useEffect, useRef, useState } from 'react'
import { ActionButton, DemoBanner, Notice } from '../../components/recruitment/RecruitmentUI'
import { loadPostInterviewSnapshot, loadResult } from './api'
import type { Finding, SavedPerformance } from './api'
import { cvCoverage, formatScoreValue, savedReport } from './assessmentView'
import { DrawerPanel, EvidenceSources } from './CandidateAssessment'
import { comparisonCriteria, comparisonWarnings, validateComparison } from './comparison'
import type { ComparisonEntry, ComparisonSelection } from './comparison'
import { timeLabel } from './format'
import './CandidateComparison.css'

const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())
const performanceDimensions = ['technical_competency', 'communication', 'response_relevance', 'problem_solving', 'experience_depth', 'job_alignment']
const findingSections = ['strengths', 'weaknesses', 'evidence_gaps', 'recommended_areas'] as const
const findingLabels = {strengths: 'Strengths', weaknesses: 'Weaknesses', evidence_gaps: 'Evidence gaps', recommended_areas: 'Verify next'}

export default function CandidateComparison({ token, jobId, jobTitle, selected, close }: {
  token: string; jobId: string; jobTitle: string; selected: ComparisonSelection[]; close: () => void
}) {
  const [entries, setEntries] = useState<ComparisonEntry[] | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [source, setSource] = useState<{candidate: number; turnId: string} | null>(null)
  const sourcePanel = useRef<HTMLElement>(null)
  const evidenceTrigger = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      setEntries(null); setError('')
      try {
        if (selected.length !== 2 || selected[0].id === selected[1].id || selected[0].runId === selected[1].runId) throw new Error('Select two different candidates with completed assessments.')
        if (selected[0].synthetic !== selected[1].synthetic) throw new Error('Compare sample candidates separately from real candidates.')
        const results = await Promise.all(selected.map(async selection => {
          const data = await loadResult(token, selection.runId, controller.signal)
          validateComparison(data, selection, jobId)
          let performance: SavedPerformance | null = null
          let performanceWarning: string | undefined
          if (data.final_score?.interview_run_id && data.final_score.interview_score_100 != null && data.run.parent_run_id) {
            try {
              const snapshot = await loadPostInterviewSnapshot(token, data.run.parent_run_id, controller.signal)
              const item = snapshot.items.find(item => item.run.id === data.final_score!.interview_run_id
                && item.run.kind === 'interview_performance' && item.run.status === 'completed'
                && item.run.job_id === jobId && item.run.parent_run_id === data.run.parent_run_id
                && item.run.application_id === data.run.application_id && item.run.synthetic === data.run.synthetic)
              if (item?.result?.dimensions && item.result.scoring_settings) performance = item.result
              else performanceWarning = 'The exact interview-performance result used by this score is unavailable.'
            } catch {
              performanceWarning = 'Interview-performance details could not be loaded. Close and reopen comparison to retry.'
            }
          }
          return {data, performance, performanceWarning}
        }))
        if (!controller.signal.aborted) setEntries(results)
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load the saved assessments.')
      }
    }
    void load()
    return () => controller.abort()
  }, [token, jobId, selected, attempt])
  useEffect(() => {
    if (source) { sourcePanel.current?.scrollIntoView({block: 'nearest'}); sourcePanel.current?.focus() }
  }, [source])

  function evidence(candidate: number, turnId: string) {
    evidenceTrigger.current = document.activeElement as HTMLElement
    setSource({candidate, turnId})
  }
  function dismissSource() { setSource(null); evidenceTrigger.current?.focus() }
  function findings(candidate: number, items: Finding[]) {
    const data = entries![candidate].data
    const report = savedReport(data)!
    return items.length ? <ul className="comparison-findings">{items.map((item, index) => <li key={index}>
      <strong>{data.criteria.find(criterion => criterion.id === item.criterion_id)?.text ?? 'Saved criterion'}</strong><p>{item.text}</p>
      <details><summary>Evidence · {item.source_ids.length} sources</summary><EvidenceSources sources={report.source_registry.filter(source => item.source_ids.includes(source.id))} onEvidence={id => evidence(candidate, id)} /></details>
    </li>)}</ul> : <p className="ca-muted">None recorded.</p>
  }
  const rows = entries ? comparisonCriteria(entries) : []
  const warnings = entries ? comparisonWarnings(entries) : []
  const sourceData = source && entries ? entries[source.candidate].data : null
  const sourceTurn = sourceData?.source_turns.find(turn => turn.id === source?.turnId)

  return <DrawerPanel title="Compare candidates" modal className="candidate-comparison" close={close}>
    <p className="comparison-intro">{jobTitle} · Two saved assessments. Scores support recruiter review; they do not rank candidates or establish equal interview difficulty.</p>
    {error ? <Notice title="Comparison unavailable" tone="danger" actions={<ActionButton onClick={() => setAttempt(value => value + 1)}>Try again</ActionButton>}>{error}</Notice>
      : !entries ? <p role="status">Loading saved assessments…</p> : <>
        {selected.every(item => item.synthetic) && <DemoBanner>Both candidates are synthetic samples. These results are separate from real candidate assessments.</DemoBanner>}
        {!!warnings.length && <details className="comparison-warnings" open><summary>Comparison limits · {warnings.length}</summary><ul>{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></details>}
        <p className="ca-muted comparison-scroll-hint">Scroll across to compare columns. “—” means unavailable or not assessed, not zero. Discussion coverage does not establish proficiency.</p>
        <div className="comparison-table-scroll" role="region" aria-label="Side-by-side candidate assessments" tabIndex={0}>
          <table className="comparison-table"><caption className="sr-only">Saved candidate assessments for {jobTitle}</caption>
            <thead><tr><th scope="col">Assessment</th>{entries.map(({data}) => <th scope="col" key={data.run.id}><strong>{data.run.label}</strong><small>Saved {new Date(data.run.created_at).toLocaleString()}</small><a href={`/recruiter/assessments?job=${encodeURIComponent(jobId)}&run=${encodeURIComponent(data.run.id)}`} target="_blank" rel="noreferrer">Open saved assessment ↗</a></th>)}</tr></thead>
            <tbody>
              <tr><th scope="row">Source versions</th>{entries.map(({data, performance}) => <td key={data.run.id}>Criteria v{data.run.profile_version}{data.outdated && ' · historical'}<br />Assessment rubric: {data.run.rubric_version}<br />Analysis: {data.run.prompt_version}<br />Interview scoring: {performance ? `revision ${performance.scoring_settings.revision}` : 'Unavailable'}{performance && <details><summary>Saved scoring policy</summary><p>Rubric: {performance.scoring_settings.rubric_version ?? 'Unavailable'}</p><p className="comparison-hash">Policy: {performance.scoring_settings.policy_sha256}</p>{Object.entries(performance.scoring_settings.policy.weights).map(([name, weight]) => <p key={name}>{label(name)}: {weight}%</p>)}</details>}</td>)}</tr>
              <tr><th scope="row">CV screening / 100</th>{entries.map(({data}) => <td key={data.run.id}><strong>{formatScoreValue(savedReport(data)!.component_scores.cv.aggregate)}</strong><details><summary>CV score breakdown</summary>{savedReport(data)!.component_scores.cv.dimensions.map(item => <p key={item.dimension}>{label(item.dimension)}: {formatScoreValue(item.score)} / 100 · {item.approved_percentage}% weight</p>)}</details></td>)}</tr>
              <tr><th scope="row">Interview performance / 100</th>{entries.map(({data}) => <td key={data.run.id}><strong>{formatScoreValue(data.final_score?.interview_score_100)}</strong></td>)}</tr>
              <tr><th scope="row">Combined score / 100</th>{entries.map(({data}) => <td key={data.run.id}><strong className="comparison-total">{formatScoreValue(data.final_score?.score_100)}</strong><p>{data.final_score?.status === 'provisional' ? 'Provisional' : 'Incomplete'}</p><p>{data.final_score?.reason ?? 'A compatible saved interview-performance result is required.'}</p>{data.final_score && <p>{data.final_score.policy.cv_weight}% CV + {data.final_score.policy.interview_weight}% interview · {data.final_score.policy.version}</p>}</td>)}</tr>
              <tr><th scope="row">Assessment rationale</th>{entries.map(({data}, index) => <td key={data.run.id}><strong>{label(savedReport(data)!.findings.suitability.position)}</strong><p>{savedReport(data)!.findings.suitability.rationale}</p><details><summary>Evidence</summary><EvidenceSources sources={savedReport(data)!.source_registry.filter(source => savedReport(data)!.findings.suitability.source_ids.includes(source.id))} onEvidence={id => evidence(index, id)} /></details></td>)}</tr>
              <tr className="comparison-section"><th colSpan={3}>Interview performance dimensions · / 100</th></tr>
              {performanceDimensions.map(dimension => <tr key={dimension}><th scope="row">{label(dimension)}</th>{entries.map(({data, performance}, index) => {
                const result = performance?.dimensions[dimension]
                return <td key={data.run.id}><strong>{formatScoreValue(result?.score == null ? null : result.score * 25)}</strong>{result && <details><summary>Explanation and evidence</summary><p>{result.justification}</p>{result.evidence.map((quote, n) => <blockquote key={n}><p>{quote.quote}</p><button type="button" className="ca-citation interview" onClick={() => evidence(index, quote.turn_id)}>View source transcript</button></blockquote>)}</details>}</td>
              })}</tr>)}
              <tr className="comparison-section"><th colSpan={3}>Competency evidence and interview coverage</th></tr>
              {rows.map(criterion => <tr key={criterion.key}><th scope="row">{criterion.text}</th>{entries.map(({data}, index) => {
                const included = data.criteria.some(item => item.id === criterion.id && item.text === criterion.text)
                const report = savedReport(data)!
                const coverage = data.result?.coverage?.find(item => item.criterion_id === criterion.id)
                const groups = coverage?.group_ids ?? data.answers.filter(answer => answer.group.criterion_ids.includes(criterion.id)).map(answer => answer.group.id)
                return <td key={data.run.id}>{!included ? <span className="ca-muted">Not in this assessment’s criteria.</span> : <><p>CV: {cvCoverage(report.source_registry, criterion.id).label}</p><p>Interview: {groups.length ? `Explored · ${groups.length} question groups` : 'Not asked'}</p><details><summary>Saved evidence</summary><EvidenceSources sources={report.source_registry.filter(source => source.criterion_ids.includes(criterion.id))} onEvidence={id => evidence(index, id)} /></details></>}</td>
              })}</tr>)}
              {findingSections.map(section => <Fragment key={section}><tr className="comparison-section"><th colSpan={3}>{findingLabels[section]}</th></tr><tr><th scope="row">Saved findings</th>{entries.map(({data}, index) => <td key={data.run.id}>{findings(index, savedReport(data)!.findings[section])}</td>)}</tr></Fragment>)}
            </tbody>
          </table>
        </div>
        {source && sourceData && <section className="comparison-source" ref={sourcePanel} tabIndex={-1} aria-label="Comparison source transcript"><header><h3>Source transcript · {sourceData.run.label}</h3><ActionButton onClick={dismissSource}>Back to comparison</ActionButton></header><p className="ca-muted">Original saved segment. Unverified timing and transcript gaps may limit interpretation.</p>{sourceTurn ? <blockquote><small>{label(sourceTurn.speaker_role)} · {timeLabel(sourceTurn.session_start_ms)}</small><p>{sourceTurn.text}</p></blockquote> : <p>This turn is unavailable in the saved transcript. Review the original assessment.</p>}</section>}
      </>}
  </DrawerPanel>
}
