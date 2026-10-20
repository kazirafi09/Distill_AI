import type { Run } from './CVResults'

export function screeningProgress(run: Run) {
  const total = Math.max(0, run.total)
  const finished = Math.min(total, Math.max(0, run.finished))
  const scored = Math.min(finished, Math.max(0, run.completed))
  const issues = finished - scored
  const active = ['queued', 'running'].includes(run.status)
  const title = ({ queued: 'Screening queued', running: 'Screening applications…', completed: 'Screening complete',
    completed_with_issues: 'Screening finished with processing issues', failed: 'Screening stopped', outdated: 'Screening results use older inputs', correction_needed: 'Screening setup needs attention',
  } as Record<string, string>)[run.status] ?? 'Screening needs attention'
  const description = active
    ? run.status === 'queued' ? 'Waiting for a screening worker. Processing continues on the server if you leave this page.'
      : 'Results update automatically. Processing continues on the server if you leave this page.'
    : run.status === 'outdated' ? 'Screen applications again to use the current criteria and CV files.'
      : run.status === 'correction_needed' ? 'Resolve the setup messages below, then start screening again.'
      : run.status === 'failed' || run.status === 'completed_with_issues' ? 'Saved results are retained. Review processing issues below, then resume screening.'
        : run.status === 'completed' ? 'Review candidate evidence and essential criteria before saving your interview shortlist.'
          : 'Review the run status and any processing messages below.'
  return { total, finished, scored, issues, active, title, description,
    attention: ['failed', 'completed_with_issues', 'outdated', 'correction_needed'].includes(run.status) }
}
