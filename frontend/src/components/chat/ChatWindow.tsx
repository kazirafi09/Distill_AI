import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

type ChatWindowProps = {
  title: string
  description?: string
  children: ReactNode
  composer?: ReactNode
  actions?: ReactNode
  busy?: boolean
  scrollKey?: string
}

export default function ChatWindow({
  title,
  description,
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
      <header className="flex flex-wrap items-center justify-between
        gap-3 border-b border-slate-200 px-5 py-4">
        <div>
          <h2 className="font-semibold text-slate-900">{title}</h2>
          {description && (
            <p className="mt-1 text-sm text-slate-500">{description}</p>
          )}
        </div>
        {actions}
      </header>

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