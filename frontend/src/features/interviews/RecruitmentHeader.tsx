import type { JobDescriptionSummary } from '../jd/api'

export default function RecruitmentHeader({ title, description, jobs, job, onSelect }: {
  title: string; description: string; jobs: JobDescriptionSummary[]; job?: JobDescriptionSummary; onSelect: (id: string) => void
}) {
  return <header className="recruitment-page-header">
    <div><p className="workspace-eyebrow">Recruitment workspace</p><h1>{title}</h1><p>{description}</p></div>
    {!!jobs.length && <label>Job description<select value={job?.id ?? ''} onChange={event => onSelect(event.target.value)}>{jobs.map(item => <option key={item.id} value={item.id}>JD {item.number} · {item.title}</option>)}</select></label>}
  </header>
}
