import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type ChatMessageProps = {
  role: 'user' | 'assistant'
  content: string
  name?: string
}

export default function ChatMessage({
  role,
  content,
  name,
}: ChatMessageProps) {
  const isUser = role === 'user'
  const label = name ?? (isUser ? 'You' : 'Distill AI')

  return (
    <article
      className={`rounded-2xl border p-4 ${
        isUser
          ? 'ml-auto max-w-[90%] border-emerald-100 bg-emerald-50'
          : 'mr-auto w-full border-slate-200 bg-slate-50'
      }`}
    >
      <p className="mb-2 text-xs font-semibold text-slate-500">
        {label}
      </p>

      {isUser ? (
        <p className="whitespace-pre-wrap break-words text-sm text-slate-800">
          {content}
        </p>
      ) : (
        <div className="markdown text-sm text-slate-800">
          <Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
            table: ({ children }) => (
              <div className="markdown-table-scroll" tabIndex={0} role="region" aria-label="Table">
                <table>{children}</table>
              </div>
            ),
          }}>{content}</Markdown>
        </div>
      )}
    </article>
  )
}
