import type { AnswerEnvelope, HealthOut, HistoryOut, Problem } from './types'

const BASE = '/api'

/** A typed failure carrying the server's SAFE message and trace id.
 *
 *  Never a raw error object: 40-frontend.mdc requires the API's message to be
 *  displayed verbatim and the trace id shown separately, so both are lifted out
 *  of the body here and nothing else from it is ever rendered. */
export class ProblemError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string
  readonly traceId: string

  constructor(problem: Problem) {
    super(problem.title)
    this.name = 'ProblemError'
    this.status = problem.status
    this.code = problem.type.split('/').pop() ?? 'internal_error'
    this.detail = problem.detail
    this.traceId = problem.trace_id
  }
}

const OFFLINE: Problem = {
  type: 'https://insurco.local/errors/network_unreachable',
  title: 'Sem conexão',
  status: 0,
  detail: 'Não foi possível falar com o serviço.',
  trace_id: '—',
}

async function readProblem(response: Response): Promise<ProblemError> {
  try {
    const body = (await response.json()) as Problem
    if (typeof body?.detail === 'string') return new ProblemError(body)
  } catch {
    /* a non-JSON error body is still an error; fall through */
  }
  return new ProblemError({
    ...OFFLINE,
    status: response.status,
    detail: 'O pedido não pôde ser concluído.',
    trace_id: response.headers.get('X-Trace-Id') ?? '—',
  })
}

export async function postMessage(
  conversationId: string,
  content: string,
  clientMessageId: string,
  signal?: AbortSignal,
): Promise<AnswerEnvelope> {
  let response: Response
  try {
    response = await fetch(`${BASE}/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content, client_message_id: clientMessageId }),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ProblemError(OFFLINE)
  }
  if (!response.ok) throw await readProblem(response)
  return (await response.json()) as AnswerEnvelope
}

export async function getHistory(conversationId: string): Promise<HistoryOut> {
  const response = await fetch(`${BASE}/conversations/${conversationId}/messages`)
  if (!response.ok) throw await readProblem(response)
  return (await response.json()) as HistoryOut
}

export async function getHealth(): Promise<HealthOut> {
  const response = await fetch(`${BASE}/healthz`)
  if (!response.ok) throw await readProblem(response)
  return (await response.json()) as HealthOut
}
