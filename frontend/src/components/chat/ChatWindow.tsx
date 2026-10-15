import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

type ChatWindowProps = {
  title: string
  children: ReactNode
  composer?: ReactNode
  actions?: ReactNode
  busy?: boolean
  scrollKey?: string
}

export default function ChatWindow({
  title,
  children,
  composer,
  actions,
  busy = false,
  scrollKey,
}: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [scrollKey])

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden
      rounded-2xl border border-slate-200 bg-white">
      {actions && (
        <header className="flex shrink-0 flex-wrap items-center justify-between
          gap-2 border-b border-slate-200 px-4 py-2">
          {actions}
        </header>
      )}

      <div
        role="log"
        aria-label={title}
        aria-busy={busy}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5"
      >
        {children}
        {busy && (
          <p role="status" className="text-sm text-slate-500">
            Distill AI is working...
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      {composer && (
        <footer className="border-t border-slate-200 bg-slate-50 p-4">
          {composer}
        </footer>
      )}
    </section>
  )
}
