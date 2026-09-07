/** T-56 / R-05: state.ts, the only module in the client with real logic.
 *
 *  It had zero tests. The whole client suite was two component files with one
 *  test each, so `npm test` stayed green no matter what this file did — and the
 *  API speaks three shapes that disagree with each other, which is exactly the
 *  kind of reconciliation that goes quietly wrong.
 *
 *  The case that motivated writing them: S11 made the degraded path REFUSE when
 *  the retrieved evidence carries policyholder identities, so a turn can now
 *  arrive as outcome=refused with degraded=true. The client computed 'degraded'
 *  only inside `outcome === 'answered'`, so that turn rendered as an ordinary
 *  amber refusal and the outage vanished from the interface.
 */

import { describe, expect, test } from 'vitest'
import { ProblemError } from './api'
import { fromEnvelope, fromHistory, fromProblem, mergeHistory, newTurn } from './state'
import type { AnswerEnvelope, HistoryMessageOut, MetaOut } from './types'

const META: MetaOut = {
  trace_id: 't-1',
  provider: 'openai',
  model: 'gpt-5.4-mini',
  latency_ms: 1800,
  cost_usd: 0.0014,
  usage: { prompt_tokens: 1360, cached_prompt_tokens: 0, completion_tokens: 89 },
  degraded: false,
  reason: null,
}

function envelope(over: Partial<AnswerEnvelope> = {}, meta: Partial<MetaOut> = {}): AnswerEnvelope {
  return {
    conversation_id: 'c-1',
    message_id: 'm-1',
    outcome: 'answered',
    answer: 'A vigência padrão é de 12 meses.',
    citations: [],
    clarification_options: [],
    meta: { ...META, ...meta },
    ...over,
  }
}

function historyRow(over: Partial<HistoryMessageOut> = {}): HistoryMessageOut {
  return {
    message_id: 'm-1',
    client_message_id: 'cm-1',
    question: 'q',
    outcome: 'answered',
    answer: 'a',
    error_code: null,
    detail: null,
    citations: [],
    degraded: false,
    reason: null,
    ...over,
  }
}

const PRIOR = newTurn('q', 'cm-1')

describe('outcome and outage are two facts, not one', () => {
  test('T-56: an answered turn during an outage is the degraded state', () => {
    const turn = fromEnvelope(envelope({}, { degraded: true, reason: 'provider_degraded' }), PRIOR)

    expect(turn.state).toBe('degraded')
    expect(turn.degraded).toBe(true)
    expect(turn.reason).toBe('provider_degraded')
  })

  test('T-56: a REFUSED turn during an outage stays refused and keeps the outage flag', () => {
    // The S11 case. Before `degraded` was carried separately this turn was
    // indistinguishable from a refusal on a perfectly healthy system, so the
    // card told the analyst the sources did not support an answer when the real
    // event was that the assistant could not be reached.
    const turn = fromEnvelope(
      envelope(
        { outcome: 'refused', answer: 'Não é possível expor dados pessoais…' },
        { degraded: true, reason: 'provider_degraded' },
      ),
      PRIOR,
    )

    expect(turn.state).toBe('refused')
    expect(turn.degraded).toBe(true)
    expect(turn.reason).toBe('provider_degraded')
  })

  test('T-56: an ordinary refusal carries no outage', () => {
    const turn = fromEnvelope(envelope({ outcome: 'refused' }), PRIOR)

    expect(turn.state).toBe('refused')
    expect(turn.degraded).toBe(false)
    expect(turn.reason).toBeUndefined()
  })

  test('T-56: the same distinction survives a history reload', () => {
    const refused = fromHistory(
      historyRow({ outcome: 'refused', degraded: true, reason: 'circuit_open' }),
    )
    const answered = fromHistory(historyRow({ degraded: true, reason: 'circuit_open' }))

    expect(refused.state).toBe('refused')
    expect(refused.degraded).toBe(true)
    expect(answered.state).toBe('degraded')
  })
})

describe('the three shapes the API speaks', () => {
  test('T-56: `pending` is idle, not an anomaly', () => {
    // What the in_flight branch returns when the turn is already being worked
    // on. Mapping it to a failure would put a Retry on a turn that is running.
    expect(fromEnvelope(envelope({ outcome: 'pending', answer: null }), PRIOR).state).toBe('idle')
    expect(fromHistory(historyRow({ outcome: 'pending' })).state).toBe('idle')
  })

  test('T-56: a problem+json is a failed turn with the safe message, not degraded', () => {
    const problem = new ProblemError({
      type: 'https://insurco.local/errors/provider_unavailable',
      title: 'Provider unavailable',
      status: 503,
      detail: 'The language-model provider is unavailable.',
      trace_id: 't-9',
    })

    const turn = fromProblem(problem, PRIOR)

    expect(turn.state).toBe('failed')
    // The code is derived from the problem `type` URL, so a new error code —
    // S11 added model_contract — reaches the client with no client change.
    expect(turn.errorCode).toBe('provider_unavailable')
    expect(turn.detail).toBe('The language-model provider is unavailable.')
    expect(turn.traceId).toBe('t-9')
    expect(turn.degraded).toBe(false)
    expect(turn.citations).toEqual([])
  })

  test('T-56: a history row is thinner than the envelope, so the merge keeps what it drops', () => {
    // clarification_options and trace_id are deliberately not persisted. Without
    // the merge a refetch blanks a card that was correct a tick earlier — the
    // product chips vanish and the trace id disappears from a red card someone
    // is mid-way through quoting.
    const known = {
      ...fromEnvelope(
        envelope({ outcome: 'needs_clarification', clarification_options: ['Auto', 'Residencial'] }),
        PRIOR,
      ),
    }

    const merged = fromHistory(historyRow({ outcome: 'needs_clarification' }), known)

    expect(merged.clarificationOptions).toEqual(['Auto', 'Residencial'])
    expect(merged.traceId).toBe('t-1')
  })
})

describe('mergeHistory', () => {
  test('T-56: a turn still in flight has no row yet and stays on screen', () => {
    const inFlight = newTurn('ainda enviando', 'cm-2')

    const merged = mergeHistory([historyRow()], [inFlight])

    expect(merged.map((t) => t.clientMessageId)).toEqual(['cm-1', 'cm-2'])
    expect(merged[1].state).toBe('sending')
  })

  test('T-56: the server row wins on status and answer', () => {
    const stale = { ...newTurn('q', 'cm-1'), state: 'sending' as const }

    const merged = mergeHistory([historyRow({ outcome: 'refused', answer: 'sem base' })], [stale])

    expect(merged).toHaveLength(1)
    expect(merged[0].state).toBe('refused')
    expect(merged[0].answer).toBe('sem base')
  })
})
