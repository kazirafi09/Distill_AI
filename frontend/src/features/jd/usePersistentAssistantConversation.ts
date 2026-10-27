import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../services/api'
import { getAssistant } from './assistantApi'
import type { AssistantConversation } from './assistantApi'

function readConversationId(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function storeConversationId(key: string, id: string | null) {
  try {
    if (id) {
      localStorage.setItem(key, id)
    } else {
      localStorage.removeItem(key)
    }
  } catch {
    // Continue using the current chat if storage is unavailable.
  }
}

export function usePersistentAssistantConversation(
  token: string,
  userId: string,
  scope?: string,
  restoreOnMount = true,
) {
  const storageKey = `distill-ai:assistant-conversation:${userId}${scope ? `:${scope}` : ''}`

  const [conversation, setConversation] =
    useState<AssistantConversation | null>(null)
  const [restoring, setRestoring] = useState(restoreOnMount)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [restoreAttempt, setRestoreAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    async function restore() {
      try {
        if (!restoreOnMount && restoreAttempt === 0) {
          storeConversationId(storageKey, null)
          return
        }
        const conversationId = readConversationId(storageKey)

        if (!conversationId) return

        const restored = await getAssistant(
          token,
          conversationId,
          controller.signal,
        )

        if (!controller.signal.aborted) {
          setConversation(restored)
          setRestoreError(null)
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setRestoreError(
            error instanceof ApiError && error.status === 401
              ? 'Your session expired. Sign out and sign in again.'
              : 'Unable to restore the assistant. Retry restoration or start a new conversation.',
          )
        }
      } finally {
        if (!controller.signal.aborted) {
          setRestoring(false)
        }
      }
    }

    void restore()

    return () => controller.abort()
  }, [token, storageKey, restoreAttempt, restoreOnMount])

  const rememberConversation = useCallback(
    (updated: AssistantConversation) => {
      storeConversationId(storageKey, updated.conversation_id)
      setConversation(updated)
      setRestoreError(null)
    },
    [storageKey],
  )

  function resetConversation() {
    storeConversationId(storageKey, null)
    setConversation(null)
    setRestoreError(null)
    setRestoring(false)
  }

  function retryRestore() {
    setRestoreError(null)
    setRestoring(true)
    setRestoreAttempt((value) => value + 1)
  }

  return {
    conversation,
    setConversation: rememberConversation,
    restoring,
    restoreError,
    resetConversation,
    retryRestore,
  }
}
