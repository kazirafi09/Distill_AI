import type { ButtonHTMLAttributes, ReactNode } from 'react'
import './RecruitmentUI.css'

export type Tone = 'success' | 'warning' | 'neutral' | 'info' | 'danger' | 'missing'
const symbols: Record<Tone, string> = { success: '✓', warning: '!', neutral: '○', info: '↻', danger: '!', missing: 'Ⅱ' }

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`recruitment-badge tone-${tone}`}><span aria-hidden="true">{symbols[tone]}</span> {children}</span>
}

export function ActionButton({ variant = 'secondary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' }) {
  return <button type="button" {...props} className={`recruitment-button variant-${variant} ${className}`} />
}

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`recruitment-panel ${className}`}>{children}</section>
}

export function Notice({ title, children, tone = 'neutral', actions }: { title: string; children: ReactNode; tone?: Tone; actions?: ReactNode }) {
  return <div className={`recruitment-notice tone-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
    <h3>{title}</h3><div>{children}</div>{actions && <div className="recruitment-notice-actions">{actions}</div>}
  </div>
}

export function DemoBanner({ children }: { children?: ReactNode }) {
  return <div className="recruitment-demo"><strong>DEMO</strong><p>{children ?? 'Sample data. These records are synthetic and are separate from real candidate assessments.'}</p></div>
}

export function CitationChip({ kind, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { kind: 'interview' | 'cv' | 'coverage' }) {
  return <button type="button" {...props} className={`recruitment-citation source-${kind}`}><strong>{kind.toUpperCase()}</strong> {children}</button>
}

export function SectionTabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return <nav className="recruitment-tabs" aria-label="Assessment sections">{tabs.map(tab =>
    <button type="button" key={tab.id} aria-current={active === tab.id ? 'page' : undefined} onClick={() => onChange(tab.id)}>{tab.label}</button>)}</nav>
}
