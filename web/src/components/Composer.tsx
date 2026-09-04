import { useState } from 'react'

export interface ComposerProps {
  busy: boolean
  onSend: (question: string, clientMessageId: string) => void
}

/** Mints the client_message_id, and it is minted exactly once per question.
 *
 *  crypto.randomUUID() rather than the uuid package: it is in every browser
 *  this targets and localhost counts as a secure context, so the dependency
 *  would buy nothing. Retry reuses the id this produced; it never asks for
 *  another one. */
export function Composer({ busy, onSend }: ComposerProps) {
  const [text, setText] = useState('')

  function submit() {
    const question = text.trim()
    if (!question || busy) return
    onSend(question, crypto.randomUUID())
    setText('')
  }

  return (
    <div className="border-t border-zinc-300 bg-white p-4">
      <div className="flex gap-2">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          rows={2}
          placeholder="Pergunte sobre coberturas, prazos ou sinistros…"
          aria-label="Pergunta"
          className="flex-1 resize-none rounded border border-zinc-400 p-2 text-sm focus:border-zinc-700 focus:outline-none"
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || text.trim() === ''}
          className="self-end rounded bg-zinc-800 px-4 py-2 text-sm font-medium text-white disabled:bg-zinc-300"
        >
          Enviar
        </button>
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        Enter envia, Shift+Enter quebra a linha.
      </p>
    </div>
  )
}
