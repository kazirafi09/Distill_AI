import { useLayoutEffect, useRef } from 'react'
import type { RefObject, TextareaHTMLAttributes } from 'react'

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  inputRef?: RefObject<HTMLTextAreaElement | null>
}

export default function AutoGrowingTextarea({ inputRef, value, style, ...props }: Props) {
  const localRef = useRef<HTMLTextAreaElement>(null)
  const ref = inputRef ?? localRef

  useLayoutEffect(() => {
    const field = ref.current
    if (!field) return
    let active = true
    function resize() {
      if (!active || !field || !field.clientWidth) return
      const css = getComputedStyle(field)
      const lineHeight = parseFloat(css.lineHeight) || parseFloat(css.fontSize) * 1.5
      const padding = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom)
      const border = parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth)
      const minHeight = Math.max(parseFloat(css.minHeight) || 0, lineHeight + padding + border)
      const maxHeight = lineHeight * 8 + padding + border
      field.style.height = 'auto'
      field.style.overflowY = 'hidden'
      const height = Math.max(minHeight, field.scrollHeight + border)
      field.style.height = `${Math.min(height, maxHeight)}px`
      field.style.overflowY = height > maxHeight ? 'auto' : 'hidden'
    }
    resize()
    let width = field.clientWidth
    const observer = new ResizeObserver(() => {
      if (field.clientWidth !== width) { width = field.clientWidth; resize() }
    })
    observer.observe(field)
    void document.fonts.ready.then(resize)
    return () => { active = false; observer.disconnect() }
  }, [ref, value])

  return <textarea {...props} ref={ref} rows={1} value={value}
    style={{ ...style, boxSizing: 'border-box', resize: 'none' }} />
}
