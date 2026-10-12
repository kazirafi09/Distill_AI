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
    <div className="flex flex-wrap items-center gap-2">
      <label className="text-sm" htmlFor="conversation-history">Conversation history</label>
      <select id="conversation-history" className="max-w-64 rounded border p-2 text-sm"
        disabled={disabled || loading} value={items.some((item) => item.conversation_id === activeId) ? activeId : ''}
        onChange={(event) => { if (event.target.value) onSelect(event.target.value) }}>
        <option value="">{loading ? 'Loading conversations...' : 'Open a conversation'}</option>
        {items.map((item) => <option key={item.conversation_id} value={item.conversation_id}>
          {new Date(item.created_at).toLocaleDateString('en-GB', { timeZone: 'Asia/Dhaka' })} — {item.title}
        </option>)}
      </select>
      <button className="button-secondary" disabled={disabled || loading || page === 0} onClick={() => setPage((value) => value - 1)}>Newer chats</button>
      <button className="button-secondary" disabled={disabled || loading || items.length < 20} onClick={() => setPage((value) => value + 1)}>Older chats</button>
      {!loading && result?.error && <p role="alert" className="text-sm text-red-700">{result.error}</p>}
    </div>
  )
}
