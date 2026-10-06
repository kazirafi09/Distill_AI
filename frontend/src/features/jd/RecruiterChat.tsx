import { useEffect, useRef, useState } from 'react'

import ChatInput from '../../components/chat/ChatInput'
import ChatMessage from '../../components/chat/ChatMessage'
import ChatWindow from '../../components/chat/ChatWindow'
import { ApiError } from '../../services/api'
import type { JDAction } from './api'
import {
  createAssistant,
  getAssistant,
  sendAssistantAction,
  sendAssistantMessage,
} from './assistantApi'
import type { AssistantConversation } from './assistantApi'
import {
  usePersistentAssistantConversation,
} from './usePersistentAssistantConversation'

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
  } = usePersistentAssistantConversation(token, userId)

  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const requestRef = useRef<AbortController | null>(null)
  const isBusy = busy || restoring

  useEffect(() => {
    return () => requestRef.current?.abort()
  }, [])

  const jd = conversation?.jd_conversation
  const allowedActions = jd?.interrupt?.payload.allowed_actions ?? []

  const canReply = allowedActions.includes('reply')
  const canRevise = allowedActions.includes('revise')
  const canRecoverSave = jd?.status === 'approved' && !jd.job_description

  const canCompose =
    !restoring &&
    !restoreError &&
    (
      !conversation ||
      conversation.mode === 'chat' ||
      canReply ||
      canRevise
    )

  const document = jd?.approved_jd || jd?.draft_jd

  async function runRequest(
    operation: (signal: AbortSignal) => Promise<AssistantConversation>,
    clearInput = false,
  ) {
    if (requestRef.current || restoring || restoreError) return

    const controller = new AbortController()
    requestRef.current = controller
    setBusy(true)
    setError(null)

    try {
      const updated = await operation(controller.signal)

      if (controller.signal.aborted) return

      setConversation(updated)

      const saved = updated.jd_conversation?.job_description

      if (saved && saved.id !== jd?.job_description?.id) {
        onSaved()
      }

      if (updated.error_code) {
        setError(
          `Assistant request failed: ${updated.error_code}. Reload before retrying.`,
        )
      } else if (clearInput) {
        setInput('')
      }
    } catch (caught) {
      if (controller.signal.aborted) return

      if (caught instanceof ApiError && caught.status === 401) {
        setError('Your session expired. Sign out and sign in again.')
      } else if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        conversation
      ) {
        try {
          const updated = await getAssistant(
            token,
            conversation.conversation_id,
            controller.signal,
          )

          if (!controller.signal.aborted) {
            setConversation(updated)
            setError(
              'The conversation was updated. Review it before continuing.',
            )
          }
        } catch {
          if (!controller.signal.aborted) {
            setError(
              'Unable to reload. Use Reload conversation before retrying.',
            )
          }
        }
      } else {
        setError(
          caught instanceof Error
            ? caught.message
            : 'Unable to complete the request. Reload before retrying.',
        )
      }
    } finally {
      if (!controller.signal.aborted) {
        requestRef.current = null
        setBusy(false)
      }
    }
  }

  function sendMessage() {
    const message = input.trim()

    if (!message || requestRef.current || !canCompose) return

    void runRequest(async (signal) => {
      let current = conversation

      if (!current) {
        current = await createAssistant(token, signal)

        if (signal.aborted) return current

        // Remember the server ID before running the LLM request.
        setConversation(current)
      }

      if (current.mode === 'jd_workflow') {
        const action = canReply ? 'reply' : canRevise ? 'revise' : null

        if (!action) {
          throw new Error('Use the JD review controls to continue.')
        }

        return sendAssistantAction(
          token,
          current,
          action,
          message,
          signal,
        )
      }

      return sendAssistantMessage(token, current, message, signal)
    }, true)
  }

  function performAction(action: JDAction) {
    if (!conversation) return

    const available =
      allowedActions.includes(action) ||
      (action === 'approve' && canRecoverSave)

    if (!available) return

    void runRequest(
      (signal) => sendAssistantAction(
        token,
        conversation,
        action,
        undefined,
        signal,
      ),
      true,
    )
  }

  function startNewConversation() {
    if (requestRef.current || restoring) return

    resetConversation()
    setInput('')
    setError(null)
  }

  const messages = conversation?.messages ?? []

  const savedMessageIndex = messages.findIndex(
    (message) =>
      message.role === 'assistant' &&
      message.content.startsWith('Approved job description saved:'),
  )

  const beforeJD =
    savedMessageIndex < 0
      ? messages
      : messages.slice(0, savedMessageIndex)

  const afterJD =
    savedMessageIndex < 0
      ? []
      : messages.slice(savedMessageIndex)

  // The initial creation request appears in both graphs.
  // Display it once in the central conversation.
  const jdMessages = (jd?.messages ?? []).filter(
    (message, index) => !(
      index === 0 &&
      message.role === 'user' &&
      messages.some(
        (parent) =>
          parent.role === 'user' &&
          parent.content === message.content,
      )
    ),
  )

  return (
    <div className="jd-chat">
      <ChatWindow
        title="Distill AI Assistant"
        description={
          jd?.job_description
            ? `Selected JD: ${jd.job_description.title}`
            : 'Create a job description, review it, then continue chatting.'
        }
        busy={isBusy}
        scrollKey={
          `${conversation?.checkpoint_id ?? 'new'}:` +
          `${jd?.checkpoint_id ?? ''}:${isBusy}`
        }
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
                    (signal) => getAssistant(
                      token,
                      conversation.conversation_id,
                      signal,
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
                  ? 'Describe the changes you want...'
                  : canReply
                    ? 'Answer the JD question...'
                    : 'Ask a question or request a job description...'
              }
            />
          ) : undefined
        }
      >
        {!conversation && !restoring && !restoreError && (
          <ChatMessage
            role="assistant"
            content="Hello! I’m your Distill AI assistant. How can I help you?"
          />
        )}

        {beforeJD.map((message, index) => (
          <ChatMessage
            key={message.id ?? `assistant-before-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {jdMessages.map((message, index) => (
          <ChatMessage
            key={message.id ?? `jd-message-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {jd?.rag_status === 'retrieved' && (
          <div
            className="rounded-xl border border-emerald-100
              bg-emerald-50 px-4 py-3"
          >
            <p className="text-xs font-semibold text-emerald-800">
              Reference excerpts supplied to the JD workflow
            </p>

            <p className="mt-1 break-words text-xs text-emerald-700">
              {[
                ...new Set(
                  (jd.reference_sources ?? []).map(
                    (source) => source.filename,
                  ),
                ),
              ].join(', ')}
            </p>
          </div>
        )}

        {document && (
          <ChatMessage
            role="assistant"
            name={
              jd?.status === 'approved'
                ? 'Approved job description'
                : 'Draft job description'
            }
            content={document}
          />
        )}

        {jd?.status === 'awaiting_review' && (
          <p className="text-sm text-slate-500">
            Revisions used: {jd.revision_count}/3.
            {canRevise
              ? ' Send a message to request changes.'
              : ' The revision limit has been reached.'}
          </p>
        )}

        {(allowedActions.includes('approve') || canRecoverSave) && (
          <button
            type="button"
            className="button-primary"
            disabled={isBusy}
            onClick={() => performAction('approve')}
          >
            {canRecoverSave
              ? 'Recover approved JD save'
              : 'Approve and save'}
          </button>
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

        {jd?.status === 'approved' && (
          <p role="status" className="text-sm text-emerald-700">
            {jd.job_description
              ? `Saved: ${jd.job_description.title} — ${
                  new Date(
                    jd.job_description.created_at,
                  ).toLocaleString('en-GB', {
                    timeZone: 'Asia/Dhaka',
                  })
                } (Bangladesh time)`
              : 'Approved. Recover the save before continuing.'}
          </p>
        )}

        {jd?.status === 'cancelled' && (
          <p role="status" className="text-sm text-slate-500">
            JD workflow cancelled. Start a new conversation for another JD.
          </p>
        )}

        {jd?.status === 'failed' && (
          <p role="alert" className="text-sm text-red-700">
            The JD workflow could not finish. Start a new conversation
            to try again.
          </p>
        )}

        {afterJD.map((message, index) => (
          <ChatMessage
            key={message.id ?? `assistant-after-${index}`}
            role={message.role}
            content={message.content}
          />
        ))}

        {(error || restoreError) && (
          <p role="alert" className="text-sm text-red-700">
            {error || restoreError}
          </p>
        )}
      </ChatWindow>
    </div>
  )
}