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
  const [result, setResult] = useState<{ key: string; items: ConversationSummary[]; error: string | null } | null>(null)
  const key = `${refreshKey}:${page}`
  const loading = result?.key !== key
  const items = loading ? [] : result?.items ?? []
  const selected = items.find((item) => item.conversation_id === activeId)
  const error = loading ? null : result?.error
  const placeholder = loading
    ? 'Loading conversations…'
    : error
      ? 'History unavailable'
      : items.length === 0
        ? page === 0 ? 'No conversations yet' : 'No older conversations'
        : 'Conversation history'
  useEffect(() => {
    const controller = new AbortController()
    void listAssistantConversations(token, page * 20, controller.signal).then((response) => {
      if (!controller.signal.aborted) setResult({ key, items: response.items, error: null })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResult({ key, items: [], error: error instanceof Error ? error.message : 'Unable to load conversations.' })
    })
    return () => controller.abort()
  }, [token, page, key])
  return (
    <div className="conversation-history">
      <label className="sr-only" htmlFor="conversation-history">Conversation history</label>
      <div className="conversation-picker">
        <svg className="conversation-picker-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 11a9 9 0 1 1 2.6 7.4M3 4v7h7" /><path d="M12 7v5l3 2" /></svg>
        <select id="conversation-history"
          title={selected?.title || placeholder}
          disabled={disabled || loading || items.length === 0}
          value={selected?.conversation_id ?? ''}
          onChange={(event) => { if (event.target.value) onSelect(event.target.value) }}>
          <option value="" disabled>{placeholder}</option>
          {items.map((item) => <option key={item.conversation_id} value={item.conversation_id}>
            {item.title.trim() || 'Untitled conversation'} · {new Date(item.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Dhaka' })}
          </option>)}
        </select>
        <svg className="conversation-picker-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </div>
      {(page > 0 || items.length >= 20) && (
        <div className="conversation-pagination" role="group" aria-label="Conversation history pages">
          <button type="button" title="Newer conversations" aria-label="Newer conversations" disabled={disabled || loading || page === 0} onClick={() => setPage((value) => value - 1)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <span aria-live="polite">{page + 1}</span>
          <button type="button" title="Older conversations" aria-label="Older conversations" disabled={disabled || loading || items.length < 20} onClick={() => setPage((value) => value + 1)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </div>
  )
}
