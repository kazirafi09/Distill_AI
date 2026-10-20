import { useEffect, useState } from 'react'
import { listAssistantConversations } from './assistantApi'
import type { ConversationSummary } from './assistantApi'

type Props = {
  token: string
  activeId?: string
  refreshKey: string
  disabled: boolean
  onSelect: (id: string) => void
}

export default function ConversationHistory({ token, activeId, refreshKey, disabled, onSelect }: Props) {
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  const [reload, setReload] = useState(0)
  const [result, setResult] = useState<{ key: string; items: ConversationSummary[]; error: string | null } | null>(null)
  const key = `${refreshKey}:${page}:${reload}`
  const loading = result?.key !== key
  const items = loading ? [] : result?.items ?? []
  const error = loading ? null : result?.error
  const visible = items.filter(item => item.title.toLowerCase().includes(query.toLowerCase()))
  useEffect(() => {
    const controller = new AbortController()
    void listAssistantConversations(token, page * 20, controller.signal).then(response => {
      if (!controller.signal.aborted) setResult({ key, items: response.items, error: null })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResult({ key, items: [], error: error instanceof Error ? error.message : 'Unable to load conversations.' })
    })
    return () => controller.abort()
  }, [token, page, key])
  return <aside className="jd-conversations" aria-label="Conversations">
    <label>Search conversations<input type="search" placeholder="Title on this page" value={query} onChange={event => setQuery(event.target.value)} /></label>
    <p className="panel-caption">All conversations</p>
    <div className="jd-conversation-list">
      {loading && <p role="status">Loading conversations…</p>}
      {error && <div role="alert"><p>{error}</p><button className="button-secondary" onClick={() => setReload(value => value + 1)}>Try again</button></div>}
      {!loading && !error && !visible.length && <p>{query ? 'No titles match on this page.' : 'No conversations on this page.'}</p>}
      {visible.map(item => <button key={item.conversation_id} className={`jd-library-item${activeId === item.conversation_id ? ' is-selected' : ''}`} disabled={disabled} aria-pressed={activeId === item.conversation_id} onClick={() => onSelect(item.conversation_id)}>
        <strong>{item.title.trim() || 'Untitled conversation'}</strong>
        <small>{new Date(item.created_at).toLocaleString('en-GB', { timeZone: 'Asia/Dhaka', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</small>
      </button>)}
    </div>
    <div className="panel-pagination"><button className="button-secondary" disabled={disabled || loading || page === 0} onClick={() => setPage(value => value - 1)}>Newer</button><button className="button-secondary" disabled={disabled || loading || !!error || items.length < 20} onClick={() => setPage(value => value + 1)}>Older</button></div>
  </aside>
}
