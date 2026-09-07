/** Hand-written mirrors of app/api/schemas.py. Kept flat and literal on purpose:
 *  a generated client would hide the one thing worth seeing here, which is that
 *  the API speaks three shapes that do not agree with each other. */

export type TurnStatus =
  | 'pending'
  | 'answered'
  | 'refused'
  | 'needs_clarification'
  | 'failed'

export interface CitationOut {
  evidence_id: string
  document_code: string
  document_title: string
  section: string
  version: string
  effective_date: string
  snippet: string
  /** D-05. 'claims' means `section` is a named database query rather than a
   *  document section, which changes what the panel calls both of them.
   *  Null only on citations written before migration 0002 recorded it. */
  source_kind: 'corpus' | 'claims' | null
  superseded: boolean
  page_from: number | null
  page_to: number | null
}

export interface MetaOut {
  trace_id: string
  provider: string
  model: string
  latency_ms: number
  cost_usd: number
  usage: {
    prompt_tokens: number
    cached_prompt_tokens: number
    completion_tokens: number
  }
  degraded: boolean
  reason: string | null
}

/** POST /conversations/{id}/messages, HTTP 200. */
export interface AnswerEnvelope {
  conversation_id: string
  message_id: string
  outcome: TurnStatus
  answer: string | null
  citations: CitationOut[]
  clarification_options: string[]
  meta: MetaOut
}

/** GET /conversations/{id}/messages. Note `degraded` and `reason` sit at the top
 *  level here and under `meta` in the envelope, and there is no trace_id at all. */
export interface HistoryMessageOut {
  message_id: string
  client_message_id: string
  question: string
  outcome: TurnStatus
  answer: string | null
  error_code: string | null
  detail: string | null
  citations: CitationOut[]
  degraded: boolean
  reason: string | null
}

export interface HistoryOut {
  conversation_id: string
  messages: HistoryMessageOut[]
}

/** RFC 9457 problem+json, every 4xx and 5xx. */
export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  trace_id: string
}

export interface HealthOut {
  status: 'ok' | 'degraded'
  breaker: {
    state: string
    consecutive_failures: number
    opened_at: number | null
    reset_in_s: number
  }
  provider: string
  storage: string
  database: { configured: boolean; reachable: boolean | null; detail: string | null }
  degraded_since: number | null
}
