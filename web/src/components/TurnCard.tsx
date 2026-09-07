import { useEffect, useState } from 'react'
import type { Turn, TurnState } from '../state'
import { CitationList } from './CitationList'

export interface TurnCardProps {
  turn: Turn
  onRetry: (turn: Turn) => void
  onCancel: (turn: Turn) => void
  onRefresh: () => void
  onChoose: (turn: Turn, product: string) => void
}

/** Badge text is the PRIMARY discriminator, not the colour.
 *
 *  The gate is "screenshot all seven states, and if two look alike, fix it" —
 *  a gate settled by screenshots is settled in greyscale, at thumbnail size, by
 *  a reader who may not separate amber from red. So every card names its own
 *  state in words and the palette only reinforces it.
 *
 *  "Sem base nas fontes" rather than "Recusado": a refusal is a statement about
 *  the corpus, not about the analyst's question, and "rejected" is exactly the
 *  misreading the amber rule exists to prevent. */
const BADGE: Record<TurnState, string> = {
  idle: 'Aguardando',
  sending: 'Consultando',
  complete: 'Resposta',
  refused: 'Sem base nas fontes',
  needs_clarification: 'Precisa de esclarecimento',
  failed: 'Falha',
  degraded: 'Resposta parcial',
}

const CARD: Record<TurnState, string> = {
  idle: 'border-2 border-dashed border-zinc-400 bg-zinc-50 text-zinc-700',
  sending: 'border border-blue-300 bg-white text-zinc-900',
  complete: 'border border-zinc-200 border-l-4 border-l-emerald-600 bg-white text-zinc-900',
  refused: 'border-2 border-amber-400 bg-amber-50 text-amber-900',
  needs_clarification: 'border-2 border-indigo-400 bg-indigo-50 text-indigo-900',
  failed: 'border-2 border-red-500 bg-red-50 text-red-900',
  degraded: 'border-2 border-slate-400 bg-slate-100 text-slate-900',
}

const BADGE_STYLE: Record<TurnState, string> = {
  idle: 'bg-zinc-200 text-zinc-800',
  sending: 'bg-blue-100 text-blue-800',
  complete: 'bg-emerald-100 text-emerald-800',
  refused: 'bg-amber-200 text-amber-900',
  needs_clarification: 'bg-indigo-200 text-indigo-900',
  failed: 'bg-red-200 text-red-900',
  degraded: 'bg-slate-300 text-slate-900',
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(id)
  }, [])
  // The counter is the point: it is what makes an 8 s budget feel honest
  // rather than broken.
  return (
    <span className="font-mono tabular-nums" aria-label="tempo decorrido">
      {((now - since) / 1000).toFixed(1)} s
    </span>
  )
}

export function TurnCard({ turn, onRetry, onCancel, onRefresh, onChoose }: TurnCardProps) {
  const { state } = turn
  return (
    <article className="mb-6" data-testid="turn">
      <p className="mb-1 text-sm text-zinc-500">
        <span className="font-medium text-zinc-700">Pergunta:</span> {turn.question}
      </p>

      <div className={`rounded p-4 ${CARD[state]}`} data-state={state}>
        <div className="mb-2 flex items-center gap-2">
          <span
            className={`rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wide ${BADGE_STYLE[state]}`}
            data-testid="badge"
          >
            {BADGE[state]}
          </span>
          {state === 'sending' && turn.startedAt !== undefined && (
            <Elapsed since={turn.startedAt} />
          )}
          {state === 'refused' && !turn.degraded && (
            <span className="text-xs text-amber-800">
              HTTP 200 — uma recusa é um resultado correto
            </span>
          )}
        </div>

        {state === 'idle' && (
          <>
            <p>
              O servidor continua processando este turno. Cancelar interrompeu a
              espera, não o trabalho.
            </p>
            <button
              type="button"
              onClick={onRefresh}
              className="mt-3 rounded border border-zinc-500 px-3 py-1 text-sm font-medium hover:bg-zinc-200"
            >
              Atualizar
            </button>
          </>
        )}

        {state === 'sending' && (
          <>
            <div className="space-y-2" aria-hidden="true">
              <div className="h-3 w-11/12 rounded bg-zinc-200" />
              <div className="h-3 w-9/12 rounded bg-zinc-200" />
              <div className="h-3 w-6/12 rounded bg-zinc-200" />
            </div>
            <button
              type="button"
              onClick={() => onCancel(turn)}
              className="mt-3 rounded border border-blue-500 px-3 py-1 text-sm font-medium text-blue-800 hover:bg-blue-50"
            >
              Cancelar
            </button>
          </>
        )}

        {state === 'complete' && (
          <>
            <p className="whitespace-pre-wrap">{turn.answer}</p>
            <CitationList citations={turn.citations} />
          </>
        )}

        {state === 'refused' && (
          <>
            {/* A refusal during an outage is a different fact from a refusal on
                a healthy system, and the analyst has to be able to tell them
                apart. Without this the card says "the sources do not support an
                answer" when what happened is that the assistant was unreachable
                and the retrieved text carried policyholder identities, so the
                excerpts could not be shown either. Said in words, because the
                card only has one palette to spend and the refusal owns it. */}
            {turn.degraded && (
              <div
                className="-m-4 mb-3 border-b border-slate-400 bg-slate-200 p-3 text-sm font-medium text-slate-900"
                data-testid="outage-banner"
              >
                Assistente indisponível{turn.reason ? ` (${turn.reason})` : ''}. Esta
                recusa não vem de uma consulta às fontes — os trechos recuperados
                contêm dados pessoais e não podem ser exibidos sem resumo.
              </div>
            )}
            <p className="whitespace-pre-wrap">{turn.answer}</p>
          </>
        )}

        {state === 'needs_clarification' && (
          <>
            <p className="whitespace-pre-wrap">{turn.answer}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {turn.clarificationOptions.map((product) => (
                <button
                  key={product}
                  type="button"
                  onClick={() => onChoose(turn, product)}
                  className="rounded-full border-2 border-indigo-500 bg-white px-4 py-1 text-sm font-semibold text-indigo-800 hover:bg-indigo-100"
                >
                  {product}
                </button>
              ))}
            </div>
          </>
        )}

        {state === 'failed' && (
          <>
            {/* The safe message, verbatim. Never a raw error object. */}
            <p className="whitespace-pre-wrap">{turn.detail}</p>
            <p className="mt-2 text-xs">
              <span className="text-red-700">trace_id</span>{' '}
              <code className="rounded bg-red-100 px-1 font-mono">{turn.traceId}</code>
            </p>
            <button
              type="button"
              onClick={() => onRetry(turn)}
              className="mt-3 rounded border-2 border-red-600 px-3 py-1 text-sm font-bold text-red-800 hover:bg-red-100"
            >
              Tentar novamente
            </button>
          </>
        )}

        {state === 'degraded' && (
          <>
            <div className="-m-4 mb-3 border-b border-slate-400 bg-slate-200 p-3 text-sm font-medium">
              Assistente indisponível{turn.reason ? ` (${turn.reason})` : ''}. Não foi
              possível gerar um resumo; seguem os trechos recuperados das fontes.
            </div>
            <p className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-600">
              Trechos recuperados
            </p>
            <CitationList citations={turn.citations} />
          </>
        )}
      </div>
    </article>
  )
}
