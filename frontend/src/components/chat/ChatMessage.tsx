import Markdown from 'react-markdown'

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
          ? 'ml-auto max-w-[90%] border-slate-200 bg-white'
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
          <Markdown skipHtml>{content}</Markdown>
        </div>
      )}
    </article>
  )
}