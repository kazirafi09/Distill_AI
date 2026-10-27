import { useEffect } from 'react'

function backdropDialog(event: MouseEvent | PointerEvent): HTMLDialogElement | null {
  const dialog = event.target
  if (!(dialog instanceof HTMLDialogElement) || !dialog.open || !dialog.matches(':modal')) return null
  const bounds = dialog.getBoundingClientRect()
  return event.clientX < bounds.left || event.clientX > bounds.right
    || event.clientY < bounds.top || event.clientY > bounds.bottom ? dialog : null
}

/** Give every native modal the same backdrop behavior as its Escape/close request. */
export function useDialogBackdropClose() {
  useEffect(() => {
    let pressedBackdrop: HTMLDialogElement | null = null
    function pointerdown(event: PointerEvent) {
      pressedBackdrop = event.button === 0 ? backdropDialog(event) : null
    }
    function cancel() { pressedBackdrop = null }
    function click(event: MouseEvent) {
      const dialog = backdropDialog(event)
      const shouldClose = dialog !== null && dialog === pressedBackdrop
      pressedBackdrop = null
      if (!shouldClose) return
      event.preventDefault()
      // requestClose preserves onCancel guards and onClose state cleanup.
      if (typeof dialog.requestClose === 'function') dialog.requestClose()
      else if (dialog.dispatchEvent(new Event('cancel', { cancelable: true }))) dialog.close()
    }
    document.addEventListener('pointerdown', pointerdown, true)
    document.addEventListener('pointercancel', cancel, true)
    document.addEventListener('click', click)
    return () => {
      document.removeEventListener('pointerdown', pointerdown, true)
      document.removeEventListener('pointercancel', cancel, true)
      document.removeEventListener('click', click)
    }
  }, [])
}
