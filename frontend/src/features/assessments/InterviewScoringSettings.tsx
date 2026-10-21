import { useEffect, useRef, useState } from 'react'
import { apiRequest, ApiError } from '../../services/api'
import { ActionButton, Notice, StatusBadge } from '../../components/recruitment/RecruitmentUI'
import './InterviewScoringSettings.css'

const performanceDimensions = ['technical_competency', 'communication', 'response_relevance', 'problem_solving', 'experience_depth', 'job_alignment'] as const
export type PerformanceDimension = typeof performanceDimensions[number]
export type InterviewPolicy = { weights: Record<PerformanceDimension, number>; minimum_assessed_dimensions: number; essential_dimensions: PerformanceDimension[] }
type Settings = { revision: number; policy: InterviewPolicy; default_policy: InterviewPolicy; job: {title: string; requirements: string} }
const labels: Record<PerformanceDimension, string> = {
  technical_competency: 'Technical competency', communication: 'Text-based communication', response_relevance: 'Response relevance',
  problem_solving: 'Problem-solving approach', experience_depth: 'Depth of experience', job_alignment: 'Job alignment',
}

export default function InterviewScoringSettings({token, jobId}: {token: string; jobId: string}) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<InterviewPolicy | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [reload, setReload] = useState(0)
  const lock = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    apiRequest<Settings>(`/assessments/jobs/${jobId}/scoring-settings`, {token, signal: controller.signal, cache: 'no-store'})
      .then(result => { if (!controller.signal.aborted) { setSettings(result); setDraft(structuredClone(result.policy)); setError(''); setConflict(false) } })
      .catch(caught => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : 'Could not load scoring settings.') })
    return () => controller.abort()
  }, [token, jobId, reload])
  const total = draft ? Object.values(draft.weights).reduce((sum, value) => sum + value, 0) : 0
  const weightedCount = draft ? Object.values(draft.weights).filter(value => value > 0).length : 0
  const valid = !!draft && total === 100 && Object.values(draft.weights).every(value => Number.isInteger(value) && value >= 0 && value <= 100)
    && Number.isInteger(draft.minimum_assessed_dimensions) && draft.minimum_assessed_dimensions >= 1
    && draft.minimum_assessed_dimensions <= weightedCount && draft.essential_dimensions.length <= draft.minimum_assessed_dimensions
    && draft.essential_dimensions.every(key => draft.weights[key] > 0)
  const changed = !!settings && !!draft && JSON.stringify(draft) !== JSON.stringify(settings.policy)
  async function save() {
    if (!settings || !draft || !valid || lock.current || conflict) return
    lock.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const saved = await apiRequest<Pick<Settings, 'revision' | 'policy'>>(`/assessments/jobs/${jobId}/scoring-settings`, {
        token, method: 'PUT', body: {expected_revision: settings.revision, policy: draft},
      })
      if (mounted.current) { setSettings({...settings, ...saved}); setDraft(structuredClone(saved.policy)); setNotice('Saved for new interview assessments. Existing results keep their original rubric.') }
    } catch (caught) {
      if (mounted.current) { setError(caught instanceof Error ? caught.message : 'Could not save scoring settings.'); setConflict(caught instanceof ApiError && caught.status === 409) }
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  return <details className="recruitment-panel interview-scoring-settings">
    <summary><span>Interview scoring settings</span>{settings && <StatusBadge tone={settings.revision ? 'info' : 'neutral'}>{settings.revision ? `Job revision ${settings.revision}` : 'Approved default'}</StatusBadge>}</summary>
    <p>Choose how interview performance is weighted for this job. Saved changes apply to new assessments. CV scores stay separate.</p>
    {error && <Notice title="Scoring settings need attention" tone="danger" actions={<ActionButton disabled={busy} onClick={() => { setSettings(null); setDraft(null); setNotice(''); setReload(value => value + 1) }}>Reload saved settings</ActionButton>}>{error}</Notice>}
    {notice && <Notice title="Interview scoring" tone="info">{notice}</Notice>}
    {!draft && !error && <p role="status">Loading scoring settings…</p>}
    {settings && draft && <>
      <details className="interview-scoring-jd"><summary>Review job requirements · {settings.job.title}</summary><p>{settings.job.requirements}</p></details>
      <fieldset disabled={busy || conflict}><legend>Dimension weights</legend>
        <div className="interview-scoring-grid">{performanceDimensions.map(key => <label key={key}>{labels[key]}
          <span><input type="number" min={0} max={100} step={1} value={Number.isNaN(draft.weights[key]) ? '' : draft.weights[key]}
            onChange={event => { const value = event.currentTarget.valueAsNumber; setNotice(''); setDraft({...draft, weights: {...draft.weights, [key]: value}}) }} />%</span>
        </label>)}</div>
        <p aria-live="polite">Total: <strong>{Number.isFinite(total) ? total : '—'} / 100%</strong></p>
        <label className="interview-scoring-minimum">Minimum assessed dimensions
          <input type="number" min={1} max={6} step={1} value={Number.isNaN(draft.minimum_assessed_dimensions) ? '' : draft.minimum_assessed_dimensions}
            onChange={event => { setNotice(''); setDraft({...draft, minimum_assessed_dimensions: event.currentTarget.valueAsNumber}) }} />
        </label>
        <p>Only dimensions with a positive weight count toward this minimum. Missing evidence never becomes a zero score.</p>
        <fieldset><legend>Dimensions required for an overall score</legend><div className="interview-scoring-essentials">{performanceDimensions.map(key => <label key={key}>
          <input type="checkbox" checked={draft.essential_dimensions.includes(key)} onChange={event => {
            setNotice(''); setDraft({...draft, essential_dimensions: performanceDimensions.filter(name => name === key ? event.target.checked : draft.essential_dimensions.includes(name))})
          }} />{labels[key]}</label>)}</div></fieldset>
        {!valid && <p className="assessment-warning" role="status">Weights must be whole percentages totalling 100. The minimum must fit the weighted dimensions and include every required dimension.</p>}
        <div className="interview-scoring-actions"><ActionButton variant="primary" disabled={!valid || !changed || busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save for this job'}</ActionButton>
          <ActionButton onClick={() => { setDraft(structuredClone(settings.default_policy)); setNotice('Default values loaded. Save to apply them to this job.') }}>Use approved defaults</ActionButton>
          {changed && <ActionButton onClick={() => { setDraft(structuredClone(settings.policy)); setNotice('') }}>Discard edits</ActionButton>}</div>
      </fieldset><p className="assessment-muted">Partial-coverage overall scores are provisional. Compare candidates using the same criteria and scoring revision.</p>
    </>}
  </details>
}
