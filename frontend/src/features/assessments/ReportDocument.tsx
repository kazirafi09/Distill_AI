import type { FullReport, ReportBlock } from './api'
import './ReportDocument.css'

function Block({ block, prefix }: { block: ReportBlock; prefix: string }) {
  const id = block.anchor ? `${prefix}-${block.anchor}` : undefined
  if (block.kind === 'heading') return <h4 id={id}>{block.text}</h4>
  if (block.kind === 'quote') return <blockquote><p>{block.text}</p>{block.citation_id && <a className="report-citation" href={`#${prefix}-${block.citation_id}`}>[{block.citation_id}]</a>}</blockquote>
  return <p className={`report-${block.kind}`}>{block.text}{block.citation_ids?.map(ref => <span key={ref}> <a className="report-citation" href={`#${prefix}-${ref}`}>[{ref}]</a></span>)}</p>
}

function Cell({ value, evidence, prefix }: { value: string; evidence: boolean; prefix: string }) {
  const refs = value.split(', ')
  if (evidence && refs.every(ref => /^E\d+$/.test(ref))) return <>{refs.map((ref, index) => <span key={ref}>{index > 0 && ', '}<a className="report-citation" href={`#${prefix}-${ref}`}>{ref}</a></span>)}</>
  return <>{value}</>
}

export default function ReportDocument({ report, prefix = 'assessment-report' }: { report: FullReport; prefix?: string }) {
  const meta = report.metadata
  const compact = report.report_type === 'recruiter'
  const versions = meta.source_versions
  const rubric = versions?.interview_rubric ?? (meta.run.kind === 'f5' ? meta.run.rubric_version : 'Not recorded')
  const version = `Criteria v${meta.run.profile_version ?? '?'} · Transcript v${versions?.transcript ?? '?'}` + (compact ? '' : ` · Interview rubric ${rubric} · Prompt ${versions?.interview_prompt ?? meta.run.prompt_version ?? '?'}`)
  const label = [meta.run.synthetic ? 'DEMO · Synthetic sample data' : 'Confidential · Recruiter review', meta.outdated ? 'Historical' : '', 'Not a hiring decision'].filter(Boolean).join(' · ')
  return <table className={`assessment-print-frame${compact ? ' report-recruiter' : ''}`} aria-label="Full assessment report document">
    <thead><tr><td><div className="report-running-header"><strong>DISTILL AI · ASSESSMENT REPORT</strong><span>{meta.candidate.name}{meta.job && ` · ${meta.job.title} (JD ${meta.job.number})`}</span><span>{label}</span></div></td></tr></thead>
    <tbody><tr><td><article className="assessment-report-document">
      <header className="report-document-heading"><h1>{report.title}</h1><h2>{meta.candidate.name}</h2>{meta.job && <p>{meta.job.title} · JD {meta.job.number}</p>}
        {meta.generated_at && <p>Generated {new Intl.DateTimeFormat(undefined, {dateStyle:'medium', timeStyle:'short'}).format(new Date(meta.generated_at))}</p>}
        <p className="report-classification">{label}</p><p className="report-version">{version}</p>
      </header>
      <nav className="report-document-contents" aria-label="Report contents">{report.sections.map((section, index) => <a key={index} href={`#${prefix}-section-${index}`}>{section.title}</a>)}</nav>
      {report.sections.map((section, index) => <section key={index} id={`${prefix}-section-${index}`} className={[section.id === 'evidence-appendix' ? 'report-evidence-appendix' : '', section.page_break_before ? 'report-page-break' : ''].filter(Boolean).join(' ')}>
        <h3>{section.title}</h3>
        {section.table && <div className="report-table-wrap"><table className="report-coverage-table"><caption>{section.table.caption ?? 'Coverage of every saved criterion'}</caption>{section.table.widths && <colgroup>{section.table.widths.map((width, i) => <col key={i} style={{width:`${width * 100}%`}} />)}</colgroup>}<thead><tr>{section.table.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
          <tbody>{section.table.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, columnIndex) => columnIndex === 0 ? <th key={columnIndex} scope="row">{value}</th> : <td key={columnIndex}><Cell value={value} evidence={section.table!.columns[columnIndex] === 'Evidence'} prefix={prefix} /></td>)}</tr>)}</tbody>
        </table></div>}
        {section.blocks.map((block, ordinal) => <Block key={ordinal} block={block} prefix={prefix} />)}
      </section>)}
    </article></td></tr></tbody>
    <tfoot><tr><td><div className="report-running-footer"><span>{label}</span><span>{version}</span></div></td></tr></tfoot>
  </table>
}
