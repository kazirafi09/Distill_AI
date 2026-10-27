import type { Candidate, Run, Workspace } from './api.ts'
import type { Tone } from '../../components/recruitment/RecruitmentUI.tsx'

export type StageStatus = { label: string; tone: Tone; key: string }
export type CandidateRow = {
  candidate: Candidate; analysis: Candidate['analysis']; assessment: Candidate['assessment']
  interview: StageStatus; transcript: StageStatus; qa: StageStatus; report: StageStatus
  run?: Run; attention: boolean; note: string; selectable: boolean; versions: string
}
export const statusOptions = [
  ['all', 'All statuses'], ['completed', 'Completed'], ['outdated', 'Older criteria'],
  ['needs_review', 'Needs review'], ['processing', 'Processing'], ['blocked', 'Blocked'],
  ['failed', 'Failed'], ['waiting', 'Waiting'], ['not_started', 'Not started'],
] as const

function runStatus(run: Candidate['analysis']): StageStatus {
  if (!run) return { label: 'Not started', tone: 'neutral', key: 'not_started' }
  if (['queued', 'running'].includes(run.status)) return { label: run.status === 'queued' ? 'Queued' : 'Processing', tone: 'info', key: 'processing' }
  if (run.status === 'completed' && run.outdated) return { label: 'Outdated', tone: 'neutral', key: 'outdated' }
  const labels: Record<string, StageStatus> = {
    completed: { label: 'Completed', tone: 'success', key: 'completed' },
    needs_review: { label: 'Needs review', tone: 'warning', key: 'needs_review' },
    failed: { label: 'Failed', tone: 'danger', key: 'failed' },
    blocked: { label: 'Blocked', tone: 'missing', key: 'blocked' },
  }
  return labels[run.status] ?? { label: 'Unavailable', tone: 'missing', key: 'blocked' }
}

export function candidateRows(workspace: Workspace): CandidateRow[] {
  return workspace.candidates.map(candidate => {
    const analysis = candidate.analysis
    const assessment = candidate.assessment
    const qa = runStatus(analysis)
    const report = assessment ? runStatus(assessment) : {
      label: 'Waiting', tone: 'neutral' as Tone, key: 'waiting',
    }
    if (!analysis && !candidate.ready) Object.assign(qa, { label: 'Blocked', tone: 'missing', key: 'blocked' })
    if (!assessment && candidate.assessment_blocker) Object.assign(report, { label: 'Blocked', tone: 'missing', key: 'blocked' })
    const interviewLabels: Record<string, string> = { ready: 'Ready', scheduled: 'Scheduled', active: 'In progress', in_progress: 'In progress', closing: 'Finishing', completed: 'Completed', cancelled: 'Cancelled' }
    const interview: StageStatus = { label: interviewLabels[candidate.interview_status ?? ''] ?? 'Not scheduled', tone: candidate.interview_status === 'completed' ? 'success' : 'neutral', key: candidate.interview_status ?? 'not_scheduled' }
    const transcript: StageStatus = candidate.ready || candidate.transcript_status === 'completed'
      ? { label: `Finalized${candidate.transcript_version ? ` v${candidate.transcript_version}` : ''}`, tone: 'success', key: 'completed' }
      : candidate.transcript_status === 'failed' ? { label: 'Failed', tone: 'danger', key: 'failed' }
        : candidate.transcript_status === 'needs_review'
          ? { label: 'Needs review', tone: 'warning', key: 'needs_review' }
          : { label: candidate.transcript_status === 'capturing' ? 'Capturing' : candidate.transcript_status === 'finalizing' ? 'Finalizing' : 'Not captured', tone: 'neutral', key: candidate.transcript_status ?? 'not_captured' }
    const run = assessment ?? analysis ?? undefined
    const attention = [qa, report, transcript].some(status => ['needs_review', 'blocked', 'failed', 'outdated'].includes(status.key))
    let note = ''
    if (report.key === 'outdated' || qa.key === 'outdated') note = 'Uses older criteria. The saved report remains readable.'
    else if (run?.status === 'needs_review') note = 'Draft held for review. Open it to inspect the saved findings.'
    else if (run?.status === 'failed') note = 'Analysis stopped. Open the saved run for recovery options.'
    else if (run && ['running', 'queued'].includes(run.status)) note = `${run.completed_groups} of ${run.total_groups || '…'} ${run.kind === 'f6' ? 'analysis branches' : 'question groups'} saved.`
    else if (!candidate.ready && !analysis) note = candidate.blocker ?? 'Waiting for a finalized transcript.'
    else if (candidate.assessment_blocker && !assessment) note = candidate.assessment_blocker
    else if (analysis?.status === 'completed' && !assessment) note = 'Q&A is readable. Open it to check assessment readiness.'
    const versions = run ? `Criteria v${run.profile_version ?? '?'} · ${run.rubric_version ?? 'Rubric unavailable'}` : 'No saved analysis versions'
    return { candidate, analysis, assessment, interview, transcript, qa, report, run, attention, note, versions,
      selectable: assessment?.status === 'completed' && !assessment.outdated && !assessment.synthetic && !candidate.synthetic }
  })
}

export function filterRows(rows: CandidateRow[], search: string, status: string, attention: boolean) {
  const query = search.trim().toLocaleLowerCase()
  return rows.filter(row => (!query || `${row.candidate.name} ${row.candidate.email ?? ''}`.toLocaleLowerCase().includes(query))
    && (status === 'all' || row.report.key === status || (status !== 'completed' && row.qa.key === status))
    && (!attention || row.attention))
}

export function toggleSelection(selected: string[], id: string): string[] {
  return selected.includes(id) ? selected.filter(item => item !== id) : selected.length < 4 ? [...selected, id] : selected
}

export function actionLabel(row: CandidateRow) {
  if (!row.run) return 'Start Q&A analysis'
  if (row.run.status === 'needs_review') return 'Review draft'
  if (['running', 'queued'].includes(row.run.status)) return 'View progress'
  if (row.run.status === 'failed') return 'View saved run'
  if (row.assessment?.outdated) return 'Open historical'
  return row.assessment ? 'Open assessment' : 'Open Q&A analysis'
}
