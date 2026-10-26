import type { JobDescriptionSummary } from '../jd/api'
import type { ReactNode } from 'react'

export default function RecruitmentHeader({ title, description, jobs, job, onSelect, actions }: {
  title: string; description: string; jobs: JobDescriptionSummary[]; job?: JobDescriptionSummary; onSelect: (id: string) => void
  actions?: ReactNode
}) {
  const picker = !!jobs.length && <label>Job description<select aria-label="Job description" value={job?.id ?? ''} onChange={event => onSelect(event.target.value)}>{jobs.map(item => <option key={item.id} value={item.id}>JD {item.number} · {item.title}</option>)}</select></label>
  return <header className="recruitment-page-header">
    <div><p className="workspace-eyebrow">Recruitment workspace</p><h1>{title}</h1><p>{description}</p></div>
    {actions ? <div className="assessment-header-actions">{picker}{actions}</div> : picker}
  </header>
}
