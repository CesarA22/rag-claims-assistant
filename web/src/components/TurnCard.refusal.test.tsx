/** T-41 / T-56 / R-02: a refusal renders amber and NOT as an error.
 *
 *  Five assertions in one test rather than five tests, because the rule allows
 *  two component tests and a class-name check alone would prove a colour when
 *  the requirement is about meaning.
 *
 *  A refusal is a CORRECT outcome at HTTP 200 — the app decided the sources do
 *  not answer the question. So the card must carry the refusal palette and not
 *  the failure one; must carry no error semantics a screen reader would
 *  announce as a failure; must offer no Retry, because asking again cannot put
 *  an answer into the corpus, and that absence is itself the statement; and
 *  must say all of it in words, which is what survives the greyscale screenshot
 *  the gate is settled by.
 */

import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { TurnCard } from './TurnCard'
import type { Turn } from '../state'

const REFUSED: Turn = {
  clientMessageId: 'cm-refusal',
  messageId: 'm-1',
  question: 'Existe desconto para pagamento do prêmio à vista? De quanto?',
  state: 'refused',
  answer:
    'As fontes recuperadas tratam do tema, mas não respondem à pergunta. ' +
    'Não há base nas fontes para afirmar esse dado.',
  citations: [],
  clarificationOptions: [],
  degraded: false,
}

const noop = () => {}

test('T-41: a refusal is amber, carries no error semantics, and offers nothing to retry', () => {
  const { container } = render(
    <TurnCard
      turn={REFUSED}
      onRetry={noop}
      onCancel={noop}
      onRefresh={noop}
      onChoose={noop}
    />,
  )
  const card = container.querySelector('[data-state="refused"]')

  // 1. The refusal palette, and demonstrably not the failure palette.
  expect(card).not.toBeNull()
  expect(card!.className).toMatch(/amber/)
  expect(card!.className).not.toMatch(/red/)

  // 2. No error semantics. A refusal is not an alert and is not invalid input.
  expect(screen.queryByRole('alert')).toBeNull()
  expect(container.querySelector('[aria-invalid]')).toBeNull()

  // 3. Nothing to retry. The absence of the affordance IS the statement, and
  //    it is the discriminator against `failed` that survives greyscale.
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.queryByText(/tentar novamente/i)).toBeNull()

  // 4. No trace id: that belongs to failures, and showing one here would frame
  //    a correct outcome as something to report to support.
  expect(screen.queryByText(/trace_id/)).toBeNull()

  // 5. It says what it is, in words.
  expect(screen.getByTestId('badge')).toHaveTextContent('Sem base nas fontes')
  expect(screen.getByText(/resultado correto/i)).toBeInTheDocument()
  expect(screen.getByText(/Não há base nas fontes/)).toBeInTheDocument()
})

/** T-56 / R-03 / R-05: a refusal DURING AN OUTAGE is a different fact.
 *
 *  S11 made the degraded path refuse when the retrieved evidence carries
 *  policyholder identities. Without this the card is byte-identical to an
 *  ordinary refusal, so it tells the analyst the sources do not support an
 *  answer when what happened is that the assistant was unreachable and the
 *  retrieved text could not be shown. It stays `refused` rather than becoming an
 *  eighth state: the outage is orthogonal to the outcome, and the seven-state
 *  screenshot gate should not grow a state that differs from another only in a
 *  banner.
 */
test('T-56: a degraded refusal says the assistant was down, not that the corpus was silent', () => {
  const { container } = render(
    <TurnCard
      turn={{
        ...REFUSED,
        answer:
          'Não é possível expor dados pessoais de segurados — nome, CPF, telefone ou e-mail.',
        degraded: true,
        reason: 'provider_degraded',
      }}
      onRetry={noop}
      onCancel={noop}
      onRefresh={noop}
      onChoose={noop}
    />,
  )

  // Still a refusal, still amber, still HTTP 200.
  expect(container.querySelector('[data-state="refused"]')).not.toBeNull()
  expect(screen.getByTestId('badge')).toHaveTextContent('Sem base nas fontes')

  // But the outage is stated, with its reason.
  const banner = screen.getByTestId('outage-banner')
  expect(banner).toHaveTextContent(/assistente indisponível/i)
  expect(banner).toHaveTextContent('provider_degraded')

  // And the line that would be actively misleading here is gone: this refusal
  // is not the corpus reporting a considered "no".
  expect(screen.queryByText(/resultado correto/i)).toBeNull()
})
