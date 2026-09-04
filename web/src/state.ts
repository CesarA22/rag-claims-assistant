/** The seam. The API speaks three shapes and they disagree; this is where they
 *  are reconciled, and it is the only real logic in the client.
 *
 *  Deliberately plain functions with no React in them, so the mapping can be
 *  reasoned about and tested without rendering anything. */

import { ProblemError } from './api'
import type { AnswerEnvelope, CitationOut, HistoryMessageOut } from './types'

export type TurnState =
  | 'idle'
  | 'sending'
  | 'complete'
  | 'refused'
  | 'needs_clarification'
  | 'failed'
  | 'degraded'

export interface Turn {
  /** The idempotency key. Minted once, reused by Retry, never regenerated. */
  clientMessageId: string
  messageId?: string
  question: string
  state: TurnState
  answer?: string
  citations: CitationOut[]
  clarificationOptions: string[]
  errorCode?: string
  /** The server's safe message, rendered verbatim. */
  detail?: string
  traceId?: string
  /** degraded only: the underlying error code. */
  reason?: string
  /** epoch ms, drives the elapsed counter while sending. */
  startedAt?: number
}

export function newTurn(question: string, clientMessageId: string): Turn {
  return {
    clientMessageId,
    question,
    state: 'sending',
    citations: [],
    clarificationOptions: [],
    startedAt: Date.now(),
  }
}

/** HTTP 200. `pending` is a real state, not an anomaly: it is what the
 *  in_flight branch returns when this turn is already being worked on. */
export function fromEnvelope(envelope: AnswerEnvelope, prior: Turn): Turn {
  const state: TurnState =
    envelope.outcome === 'answered'
      ? envelope.meta.degraded
        ? 'degraded'
        : 'complete'
      : envelope.outcome === 'pending'
        ? 'idle'
        : envelope.outcome
  return {
    ...prior,
    messageId: envelope.message_id,
    state,
    answer: envelope.answer ?? undefined,
    citations: envelope.citations,
    clarificationOptions: envelope.clarification_options,
    traceId: envelope.meta.trace_id,
    reason: envelope.meta.reason ?? undefined,
    errorCode: undefined,
    detail: undefined,
    startedAt: undefined,
  }
}

/** Every 4xx and 5xx, plus an unreachable API. */
export function fromProblem(error: ProblemError, prior: Turn): Turn {
  return {
    ...prior,
    state: 'failed',
    answer: undefined,
    citations: [],
    clarificationOptions: [],
    errorCode: error.code,
    detail: error.detail,
    traceId: error.traceId,
    startedAt: undefined,
  }
}

/** GET, merged over what the client already knows.
 *
 *  A history row is THINNER than the envelope that produced it:
 *  clarification_options, trace_id and the citation page numbers are
 *  deliberately not persisted. Without the merge a refetch would blank a card
 *  that was correct a tick earlier — chips vanish, the trace id disappears from
 *  a red card someone is mid-way through quoting. The server row wins on
 *  status, answer and citations; `prior` supplies the rest. */
export function fromHistory(message: HistoryMessageOut, prior?: Turn): Turn {
  const state: TurnState =
    message.outcome === 'answered'
      ? message.degraded
        ? 'degraded'
        : 'complete'
      : message.outcome === 'pending'
        ? 'idle'
        : message.outcome
  return {
    clientMessageId: message.client_message_id,
    messageId: message.message_id,
    question: message.question,
    state,
    answer: message.answer ?? undefined,
    citations: message.citations,
    clarificationOptions: prior?.clarificationOptions ?? [],
    errorCode: message.error_code ?? undefined,
    detail: message.detail ?? undefined,
    traceId: prior?.traceId,
    reason: message.reason ?? undefined,
  }
}

export function mergeHistory(rows: HistoryMessageOut[], known: Turn[]): Turn[] {
  const byKey = new Map(known.map((turn) => [turn.clientMessageId, turn]))
  const merged = rows.map((row) => fromHistory(row, byKey.get(row.client_message_id)))
  const seen = new Set(rows.map((row) => row.client_message_id))
  // A turn still in flight has no row yet; keep it on screen.
  return [...merged, ...known.filter((turn) => !seen.has(turn.clientMessageId))]
}
