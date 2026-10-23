import { Children, cloneElement, isValidElement } from 'react'
import type { ReactNode, ReactElement } from 'react'
import Markdown from 'react-markdown'
import type { EditableProfile, ScreeningProfile } from './api'
import type { WindTunnelSuggestion } from './windTunnelApi'
import SuggestionChanges from './SuggestionChanges'
import { displayValue } from './windTunnelView'

type Props = {
  title: string; position?: string; content: string; profile: EditableProfile; original: ScreeningProfile | null
  patches: WindTunnelSuggestion['patches']; working: boolean; conflict: boolean; error: string
  reviewed: boolean; onReviewed: (value: boolean) => void; onBack: () => void; onApply: () => void
}

function highlight(children: ReactNode, patches: Props['patches']): ReactNode {
  return Children.map(children, child => {
    if (typeof child === 'string') {
      const patch = patches.find(item => item.kind === 'description' && typeof item.proposed === 'string' && child.includes(item.proposed))
      if (!patch || typeof patch.proposed !== 'string') return child
      const start = child.indexOf(patch.proposed)
      return <>{child.slice(0, start)}<del className="suggestion-removed">{String(patch.expected)}</del>{' '}<ins className="suggestion-added">{patch.proposed}</ins>{child.slice(start + patch.proposed.length)}</>
    }
    if (isValidElement(child)) {
      const element = child as ReactElement<{ children?: ReactNode }>
      return cloneElement(element, {}, highlight(element.props.children, patches))
    }
    return child
  })
}

export default function SuggestionReview({ title, position, content, profile, original, patches, working, conflict, error, reviewed, onReviewed, onBack, onApply }: Props) {
  return <section className="jd-suggestions jd-suggestion-review" aria-label="Review suggestion" aria-busy={working}>
    <div className="suggestion-review-top"><button className="button-secondary" disabled={working} onClick={onBack}>← Back to suggestions</button>{position && <small>{position}</small>}</div>
    <header><div><h3>{title}</h3><p>This proposal is fixed. You cannot edit it here. Cancel saves nothing.</p></div></header>
    {conflict && <div className="suggestion-alert is-warning" role="alert"><span aria-hidden="true">!</span><p><strong>The job changed or became locked after you started this review.</strong> Apply is turned off. Go back and start a fresh review of the current version.</p></div>}
    <section className="suggestion-review-section"><header><h4>Description</h4><small><span className="suggestion-diff-label is-previous">Previous</span> <span className="suggestion-diff-label is-proposed">Proposed</span> · changes are marked</small></header>
      <div className="markdown"><Markdown skipHtml components={{ p: ({ children }) => <p>{highlight(children, patches)}</p>, li: ({ children }) => <li>{highlight(children, patches)}</li> }}>{content}</Markdown></div>
    </section>
    <section className="suggestion-review-section"><h4>Screening criteria</h4><div className="suggestion-table-scroll"><table className="suggestion-criteria-table"><thead><tr><th scope="col">Criterion</th><th scope="col">Priority</th><th scope="col">Dimension</th><th scope="col">Change</th></tr></thead>
      <tbody>{profile.criteria.map(criterion => {
        const old = original?.criteria.find(item => item.id === criterion.id)
        const changed = !old || old.text !== criterion.text || old.priority !== criterion.priority || old.dimension !== criterion.dimension
        return <tr key={criterion.id} className={changed ? 'is-changed' : undefined}><th scope="row">{criterion.text}{changed && <span className="suggestion-status is-success">Changed</span>}</th><td>{displayValue(criterion.priority)}</td><td>{criterion.dimension.replaceAll('_', ' ')}</td><td>{!old ? 'Added' : changed ? `Previously: ${old.text} · ${displayValue(old.priority)} · ${old.dimension.replaceAll('_', ' ')}` : 'Unchanged'}</td></tr>
      })}</tbody></table></div>
      <h4>Dimension weights</h4><div className="suggestion-table-scroll"><table className="suggestion-criteria-table"><thead><tr><th scope="col">Dimension</th><th scope="col">Previous</th><th scope="col">Proposed</th></tr></thead><tbody>{Object.entries(profile.dimension_weights).map(([key, value]) => <tr key={key} className={original?.dimension_weights[key] !== value ? 'is-changed' : undefined}><th scope="row">{key.replaceAll('_', ' ')}</th><td>{original?.dimension_weights[key] ?? 0}%</td><td>{value}%</td></tr>)}</tbody></table></div>
      <h4>Experience and application details</h4><dl className="suggestion-metadata">{(['minimum_years_experience', 'fresher_eligibility', 'google_form_url'] as const).map(key => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{displayValue(profile[key])}{original?.[key] !== profile[key] && <small>Previously: {displayValue(original?.[key])}</small>}</dd></div>)}</dl>
    </section>
    <section className="suggestion-review-section"><h4>Exact changes</h4><SuggestionChanges patches={patches} profile={original} /></section>
    <footer className="suggestion-review-footer">
      <label className="jd-review-check"><input type="checkbox" checked={reviewed} disabled={working || conflict} onChange={event => onReviewed(event.target.checked)} />I reviewed the description and screening criteria together.</label>
      {error && !conflict && <p className="ui-error" role="alert">{error}</p>}
      <p className="muted">Applying saves both sections together. It does not publish the job or start a new batch.</p>
      <div className="suggestion-review-actions"><button className="button-secondary" disabled={working} onClick={onBack}>Cancel review</button><button className="button-primary" disabled={working || conflict || !reviewed} onClick={onApply}>{working ? 'Applying…' : 'Apply suggestion'}</button></div>
    </footer>
  </section>
}
