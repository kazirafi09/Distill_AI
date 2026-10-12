type ChatInputProps = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  disabled?: boolean
  placeholder?: string
}

export default function ChatInput({
  value,
  onChange,
  onSend,
  disabled = false,
  placeholder = 'Type your message...',
}: ChatInputProps) {
  function send() {
    if (!disabled && value.trim()) {
      onSend()
    }
  }

  return (
    <form
      className="rounded-2xl border border-slate-200 bg-white p-3"
      onSubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      <textarea
        aria-label="Chat message"
        className="w-full resize-none rounded-xl px-2 py-2 text-sm
          text-slate-800 placeholder:text-slate-400 disabled:opacity-60"
        rows={3}
        maxLength={6000}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === 'Enter' &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault()
            send()
          }
        }}
      />

      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">
          Enter to send · Shift+Enter for a new line
        </span>

        <button
          type="submit"
          className="button-primary"
          disabled={disabled || !value.trim()}
        >
          Send
        </button>
      </div>
    </form>
  )
}