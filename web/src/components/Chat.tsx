import { useCallback, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ProblemError, getHistory, postMessage } from '../api'
import { fromEnvelope, fromProblem, mergeHistory, newTurn, type Turn } from '../state'
import { Composer } from './Composer'
import { ConnectionBanner } from './ConnectionBanner'
import { TurnCard } from './TurnCard'

const CONVERSATION_ID = 'web-1'

const SEEDS = [
  'Qual é a vigência padrão da apólice de seguro auto?',
  'Qual é o limite da cobertura de vidros?',
  'Qual o preço do bitcoin hoje?',
]

export function Chat() {
  const [turns, setTurns] = useState<Turn[]>([])
  const queryClient = useQueryClient()
  // One controller per in-flight client_message_id, so Cancel aborts exactly
  // the turn whose button was pressed.
  const inFlight = useRef(new Map<string, AbortController>())

  const upsert = useCallback((turn: Turn) => {
    setTurns((current) => {
      const index = current.findIndex((t) => t.clientMessageId === turn.clientMessageId)
      if (index === -1) return [...current, turn]
      const next = [...current]
      next[index] = turn
      return next
    })
  }, [])

  const send = useCallback(
    async (question: string, clientMessageId: string) => {
      const pending = newTurn(question, clientMessageId)
      upsert(pending)
      const controller = new AbortController()
      inFlight.current.set(clientMessageId, controller)
      try {
        const envelope = await postMessage(
          CONVERSATION_ID,
          question,
          clientMessageId,
          controller.signal,
        )
        upsert(fromEnvelope(envelope, pending))
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          // The server did not stop. `idle` is the state that says so.
          upsert({ ...pending, state: 'idle', startedAt: undefined })
        } else if (error instanceof ProblemError) {
          upsert(fromProblem(error, pending))
        } else {
          throw error
        }
      } finally {
        inFlight.current.delete(clientMessageId)
      }
    },
    [upsert],
  )

  /** Retry re-posts the SAME client_message_id. That is the whole mechanism:
   *  the server re-opens the failed row rather than creating a second one. */
  const retry = useCallback(
    (turn: Turn) => {
      void send(turn.question, turn.clientMessageId)
    },
    [send],
  )

  const cancel = useCallback((turn: Turn) => {
    inFlight.current.get(turn.clientMessageId)?.abort()
  }, [])

  const refresh = useCallback(async () => {
    const history = await queryClient.fetchQuery({
      queryKey: ['history', CONVERSATION_ID],
      queryFn: () => getHistory(CONVERSATION_ID),
      staleTime: 0,
    })
    setTurns((current) => mergeHistory(history.messages, current))
  }, [queryClient])

  /** A chip appends the product, which is what makes the clarification a
   *  working control rather than an acknowledgement: grounding.names_a_product
   *  then matches and the same question becomes answerable. New question, new
   *  key — this is a different turn, not a retry of the old one. */
  const choose = useCallback(
    (turn: Turn, product: string) => {
      void send(`${turn.question} (${product})`, crypto.randomUUID())
    },
    [send],
  )

  const busy = turns.some((turn) => turn.state === 'sending')

  return (
    <div className="mx-auto flex h-screen max-w-3xl flex-col border-x border-zinc-300 bg-zinc-50">
      <ConnectionBanner />
      <header className="border-b border-zinc-300 bg-white px-4 py-3">
        <h1 className="text-lg font-semibold text-zinc-900">
          InsurCo — assistente de sinistros
        </h1>
        <p className="text-xs text-zinc-500">
          Responde apenas a partir do corpus controlado e do banco de sinistros, sempre
          com citação.
        </p>
      </header>

      <main className="flex-1 overflow-y-auto p-4">
        {turns.length === 0 && (
          <div className="rounded border-2 border-dashed border-zinc-300 p-6 text-sm text-zinc-500">
            <p className="mb-3 font-medium text-zinc-700">Nenhuma pergunta ainda.</p>
            <ul className="space-y-1">
              {SEEDS.map((seed) => (
                <li key={seed}>
                  <button
                    type="button"
                    onClick={() => void send(seed, crypto.randomUUID())}
                    className="text-left text-zinc-600 underline decoration-dotted hover:text-zinc-900"
                  >
                    {seed}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {turns.map((turn) => (
          <TurnCard
            key={turn.clientMessageId}
            turn={turn}
            onRetry={retry}
            onCancel={cancel}
            onRefresh={() => void refresh()}
            onChoose={choose}
          />
        ))}
      </main>

      <Composer busy={busy} onSend={(question, id) => void send(question, id)} />
    </div>
  )
}
