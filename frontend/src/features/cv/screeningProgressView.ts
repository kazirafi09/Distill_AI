import type { Run } from './CVResults'

export function screeningProgress(run: Run) {
  const total = Math.max(0, run.total)
  const finished = Math.min(total, Math.max(0, run.finished))
  const scored = Math.min(finished, Math.max(0, run.completed))
  const issues = finished - scored
  const active = ['queued', 'running'].includes(run.status)
  const metrics = run.progress
  const phaseTitles: Record<string, string> = { parsing: 'Reading CVs', indexing: 'Preparing CV evidence', matching: 'Matching screening criteria', scoring: 'Calculating candidate scores', ranking: 'Finalizing candidate ranking' }
  const title = ({ queued: 'Screening queued', running: 'Screening applications…', completed: 'Screening complete',
    completed_with_issues: 'Screening finished with processing issues', failed: 'Screening stopped', outdated: 'Screening results use older inputs', correction_needed: 'Screening setup needs attention',
  } as Record<string, string>)[run.status] ?? 'Screening needs attention'
  const description = active
    ? run.status === 'queued' ? 'Waiting for a screening worker. Processing continues on the server if you leave this page.'
      : 'CVs are parsed, matched against the criteria and scored automatically. Processing continues on the server if you leave this page.'
    : run.status === 'outdated' ? 'Screen applications again to use the current criteria and CV files.'
      : run.status === 'correction_needed' ? 'Resolve the setup messages below, then start screening again.'
      : run.status === 'failed' || run.status === 'completed_with_issues' ? 'Saved results are retained. Review processing issues below, then resume screening.'
        : run.status === 'completed' ? 'Review candidate evidence and essential criteria before saving your interview shortlist.'
          : 'Review the run status and any processing messages below.'
  const stages = [
    { id: 'read', label: 'Read CVs', detail: 'Extract text and build candidate profiles', done: metrics?.parsed, total, skipped: metrics?.read_skipped ?? 0, current: metrics?.stage === 'parsing', unit: 'applications' },
    { id: 'match', label: 'Match criteria', detail: metrics?.stage === 'indexing' ? 'Preparing source evidence for matching' : 'Check CV evidence against each requirement', done: metrics?.criteria_completed, total: metrics?.criteria_total, skipped: metrics?.criteria_skipped ?? 0, current: ['indexing', 'matching'].includes(metrics?.stage ?? ''), unit: 'checks' },
    { id: 'score', label: 'Score candidates', detail: 'Calculate scores using the saved weights', done: metrics?.scored ?? scored, total, skipped: metrics?.score_skipped ?? 0, current: metrics?.stage === 'scoring' || metrics?.stage === 'ranking', unit: 'candidates' },
  ].map(stage => ({ ...stage, state: stage.total && stage.done !== undefined && stage.done >= stage.total && !stage.skipped ? 'done'
    : run.status === 'running' && stage.current ? 'active' : stage.skipped ? 'issue' : 'waiting' }))
  const percentage = metrics && metrics.total_units > 0
    ? Math.min(100, Math.floor(metrics.completed_units / metrics.total_units * 100)) : null
  return { total, finished, scored: metrics?.scored ?? scored, issues, active,
    title: run.status === 'running' && metrics?.stage ? phaseTitles[metrics.stage] ?? title : title,
    stages, percentage, completedUnits: metrics?.completed_units ?? 0, skippedUnits: metrics?.skipped_units ?? 0, description,
    attention: ['failed', 'completed_with_issues', 'outdated', 'correction_needed'].includes(run.status) }
}
