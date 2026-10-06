import { useRef, useState } from 'react'

import ChatInput from '../../components/chat/ChatInput'
import ChatMessage from '../../components/chat/ChatMessage'
import ChatWindow from '../../components/chat/ChatWindow'
import { ApiError } from '../../services/api'
import {
  createConversation,
  getConversation,
  sendConversationAction,
} from './api'
import type { JDAction, JDConversation } from './api'
import { usePersistentConversation } from './usePersistentConversation'

type RecruiterChatProps = {
  token: string
  userId: string
  onSaved: () => void
}

export default function RecruiterChat({
  token,
  userId,
  onSaved,
}: RecruiterChatProps) {
  const {
    conversation,
    setConversation,
    restoring,
    restoreError,
    resetConversation,
    retryRestore,
  } = usePersistentConversation(token, userId)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestRef = useRef(false)
  const isBusy = busy || restoring

  const allowedActions =
    conversation?.interrupt?.payload.allowed_actions ?? []

  const canReply = allowedActions.includes('reply')
  const canRevise = allowedActions.includes('revise')
  const canCompose =
  !restoring &&
  !restoreError &&
  (!conversation || canReply || canRevise)

  const document =
    conversation?.approved_jd || conversation?.draft_jd

  async function runRequest(
    operation: () => Promise<JDConversation>,
    clearInput = false,
  ) {
    if (requestRef.current || restoring || restoreError) return

    requestRef.current = true
    setBusy(true)
    setError(null)

    try {
      const updated = await operation()
      setConversation(updated)
      if (
        updated.job_description &&
        updated.job_description.id !== conversation?.job_description?.id
      ) {
        onSaved()
      }
      if (clearInput) {
        setInput('')
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setError('Your session expired. Sign out and sign in again.')
      } else if (
        error instanceof ApiError &&
        error.status === 409 &&
        conversation
      ) {
        try {
          const updated = await getConversation(
            token,
            conversation.conversation_id,
          )
          setConversation(updated)
          setError(
            'The conversation was updated. Review it before continuing.',
          )
        } catch {
          setError(
            'Unable to reload the conversation. Use Reload conversation.',
          )
        }
      } else {
        setError(
          error instanceof Error
            ? error.message
            : 'Unable to complete the request. Please try again.',
        )
      }
    } finally {
      requestRef.current = false
      setBusy(false)
    }
  }

  function sendMessage() {
    const message = input.trim()

    if (!message || requestRef.current) return

    if (!conversation) {
      void runRequest(
        () => createConversation(token, message),
        true,
      )
      return
    }

    const action = canReply ? 'reply' : canRevise ? 'revise' : null

    if (action) {
      void runRequest(
        () => sendConversationAction(
          token,
          conversation,
          action,
          message,
        ),
        true,
      )
    }
  }

  function performAction(action: JDAction) {
    if (!conversation || !allowedActions.includes(action)) return

    void runRequest(
      () => sendConversationAction(token, conversation, action),
      true,
    )
  }

  function startNewConversation() {
    if (requestRef.current || restoring) return

    resetConversation()
    setInput('')
    setError(null)
  }

  return (
    <div className="jd-chat">
      <ChatWindow
        title="Job description assistant"
        description="Describe the role, answer questions, then review the draft."
        busy={isBusy}
        scrollKey={`${conversation?.checkpoint_id ?? 'new'}:${isBusy}`}
        actions={
          <div className="flex flex-wrap gap-2">
            {restoreError && (
              <button
                type="button"
                className="button-secondary"
                disabled={isBusy}
                onClick={retryRestore}
              >
                Retry restoration
              </button>
            )}
            {conversation && (
              <button
                type="button"
                className="button-secondary"
                disabled={isBusy}
                onClick={() => {
                  void runRequest(
                    () => getConversation(
                      token,
                      conversation.conversation_id,
                    ),
                  )
                }}
              >
                Reload conversation
              </button>
            )}

            <button
              type="button"
              className="button-secondary"
              disabled={isBusy}
              onClick={startNewConversation}
            >
              Create new JD
            </button>
          </div>
        }
        composer={
          canCompose ? (
            <ChatInput
              value={input}
              onChange={setInput}
              onSend={sendMessage}
              disabled={isBusy}
              placeholder={
                canRevise
                  ? 'Describe the changes you want, then press Send...'
                  : 'Tell me about the job you want to create...'
              }
            />
          ) : undefined
        }
            >
        {conversation?.rag_status === 'retrieved' && (
          <div
            className="rounded-xl border border-emerald-100
              bg-emerald-50 px-4 py-3"
          >
            <p className="text-xs font-semibold text-emerald-800">
              Reference excerpts supplied to the assistant
            </p>

            <p className="mt-1 break-words text-xs text-emerald-700">
              {[
                ...new Set(
                  (conversation.reference_sources ?? []).map(
                    (source) => source.filename,
                  ),
                ),
              ].join(', ')}
            </p>
          </div>
        )}

        {!conversation && !restoring && !restoreError && (
          <ChatMessage
            role="assistant"
            content="What job description would you like to create?"
          />
        )}

        {conversation?.messages.map((message, index) => (
          <ChatMessage
            key={message.id ?? `message-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {document && (
          <ChatMessage
            role="assistant"
            name={
              conversation?.status === 'approved'
                ? 'Approved job description'
                : 'Draft job description'
            }
            content={document}
          />
        )}

        {conversation?.status === 'awaiting_review' && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              Revisions used: {conversation.revision_count}/3.
              {canRevise
                ? ' Send a message below to request changes.'
                : ' The revision limit has been reached.'}
            </p>

            <div className="flex flex-wrap gap-2">
              {allowedActions.includes('approve') && (
                <button
                  type="button"
                  className="button-primary"
                  disabled={isBusy}
                  onClick={() => performAction('approve')}
                >
                  Approve and save
                </button>
              )}
            </div>
          </div>
        )}

        {allowedActions.includes('cancel') && (
          <button
            type="button"
            className="button-secondary"
            disabled={isBusy}
            onClick={() => performAction('cancel')}
          >
            Cancel
          </button>
        )}

        {conversation?.status === 'approved' && (
          <p role="status" className="text-sm text-emerald-700">
            {conversation.job_description
              ? `Saved: ${conversation.job_description.title} — ${
                  new Date(
                    conversation.job_description.created_at,
                  ).toLocaleString('en-GB', {
                    timeZone: 'Asia/Dhaka',
                  })
                } (Bangladesh time)`
              : 'Approved. Reload to check whether the JD was saved.'}
          </p>
        )}

        {conversation?.status === 'cancelled' && (
          <p role="status" className="text-sm text-slate-500">
            Conversation cancelled. Start a new conversation to create a JD.
          </p>
        )}

        {conversation?.status === 'failed' && (
          <p role="alert" className="text-sm text-red-700">
            The JD workflow could not finish. Start a new conversation
            to try again.
          </p>
        )}

        {(error || restoreError) && (
          <p role="alert" className="text-sm text-red-700">
            {error || restoreError}
          </p>
        )}
      </ChatWindow>
    </div>
  )
}