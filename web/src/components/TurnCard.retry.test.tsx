/** T-42 / D-02: a failure renders a WORKING Retry.
 *
 *  "Working" is asserted against the request, not against the rendering. A
 *  button that looks right and posts a fresh uuid would pass any weaker test
 *  and would silently create a second row and a second paid provider call —
 *  the exact duplication D-02 exists to prevent.
 *
 *  So this mounts the real Chat, stubs fetch, drives a question to a 503, and
 *  then asserts the click re-posts with the SAME client_message_id the first
 *  attempt used. The card must also carry the two things the rule names beside
 *  the button: the safe message verbatim and the trace id, shown separately.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { Chat } from './Chat'

const PROBLEM = {
  type: 'https://insurco.local/errors/provider_unavailable',
  title: 'Provider unavailable',
  status: 503,
  detail: 'The language-model provider is unavailable.',
  trace_id: 'trace-7f3a91',
}

function problemResponse() {
  return new Response(JSON.stringify(PROBLEM), {
    status: 503,
    headers: { 'content-type': 'application/problem+json' },
  })
}

/** Records every POST body so the retry can be compared against the original. */
function stubFetch() {
  const posts: Array<{ content: string; client_message_id: string }> = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/healthz')) {
      return new Response(
        JSON.stringify({
          status: 'ok',
          breaker: { state: 'closed', consecutive_failures: 0, opened_at: null, reset_in_s: 0 },
          provider: 'chaos',
          storage: 'sql',
          database: { configured: true, reachable: true, detail: null },
          degraded_since: null,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )
    }
    if (init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)))
      return problemResponse()
    }
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return posts
}

afterEach(() => vi.unstubAllGlobals())

test('T-42: Retry re-posts the same client_message_id, beside the safe message and trace id', async () => {
  const posts = stubFetch()
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Chat />
    </QueryClientProvider>,
  )

  await user.type(screen.getByLabelText('Pergunta'), 'Qual o preço do bitcoin hoje?')
  await user.click(screen.getByRole('button', { name: 'Enviar' }))

  const retry = await screen.findByRole('button', { name: /tentar novamente/i })
  expect(posts).toHaveLength(1)
  const original = posts[0].client_message_id
  expect(original).toBeTruthy()

  // The rule: the safe message displayed verbatim, and the trace id separately.
  expect(screen.getByText(PROBLEM.detail)).toBeInTheDocument()
  expect(screen.getByText(PROBLEM.trace_id)).toBeInTheDocument()
  // And never the raw error object.
  expect(screen.queryByText(/insurco\.local\/errors/)).toBeNull()

  await user.click(retry)

  await waitFor(() => expect(posts).toHaveLength(2))
  // The whole point: same key, so the server re-opens the failed row instead of
  // creating a second turn.
  expect(posts[1].client_message_id).toBe(original)
  expect(posts[1].content).toBe(posts[0].content)
})
