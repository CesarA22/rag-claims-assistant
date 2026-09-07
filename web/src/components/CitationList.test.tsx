/** T-27 / D-05: the provenance footer says which source, and how fresh it is.
 *
 *  D-05 asks each answer to indicate which source it came from and the freshness
 *  of the queried data. The panel already showed document, section, version and
 *  effective date — but it showed them in one vocabulary, and the two sources do
 *  not share one. `effective_date` on a corpus chunk is the date the version came
 *  INTO force; on a database row it is the date the snapshot stops. "Vigente
 *  desde 2026-02-11" on a claims citation is not a small wording problem, it is
 *  the wrong claim about the data.
 *
 *  This was also the register's phantom: D-05 listed `tests: [T-27]` and T-27
 *  existed nowhere in the repository. The traceability check now fails on a
 *  listed id with no test behind it, so this file and that rule land together.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { CitationList } from './CitationList'
import type { CitationOut } from '../types'

const CORPUS: CitationOut = {
  evidence_id: 'cg-auto-2024#v3.2#tabela-1',
  document_code: 'CG-AUTO-2024',
  document_title: 'Condições Gerais do Seguro Auto',
  section: 'Tabela 1 - Coberturas, limites e franquias',
  version: '3.2',
  effective_date: '2024-01-01',
  snippet: 'RCF-DM ... limite R$ 100.000,00',
  source_kind: 'corpus',
  superseded: false,
  page_from: 12,
  page_to: 12,
}

const DATABASE: CitationOut = {
  evidence_id: 'claims:get_claim_payment:119998a9',
  document_code: 'claims.db',
  document_title: 'Banco de sinistros Indicium InsurCo',
  section: 'claims.get_claim_payment(claim_number="SIN-2025-004512")',
  version: 'snapshot',
  effective_date: '2026-02-11',
  snippet: 'paid_amount: R$ 100.000,00',
  source_kind: 'claims',
  superseded: false,
  page_from: null,
  page_to: null,
}

const STALE: CitationOut = {
  ...CORPUS,
  evidence_id: 'ni-014-v1#3',
  document_code: 'NI-014',
  document_title: 'Normativo Interno de Prazos',
  section: '3 Prazo de Comunicação',
  version: '1.0',
  effective_date: '2023-01-01',
  snippet: 'prazo de 5 dias úteis',
  superseded: true,
}

test('T-27: a database citation names the query and the date its data stops', async () => {
  render(<CitationList citations={[DATABASE]} />)

  // The chip says what kind of source it is before anything is opened —
  // "claims.db" is a filename, not a provenance statement.
  await userEvent.click(screen.getByRole('button', { name: /banco de sinistros/i }))

  expect(screen.getByText('Fonte')).toBeInTheDocument()
  expect(screen.getByText('Consulta')).toBeInTheDocument()
  expect(
    screen.getByText('claims.get_claim_payment(claim_number="SIN-2025-004512")'),
  ).toBeInTheDocument()

  // The freshness half of D-05, in the words a snapshot deserves.
  expect(screen.getByText('Dados até')).toBeInTheDocument()
  expect(screen.getByText('2026-02-11')).toBeInTheDocument()
  expect(screen.queryByText('Vigente desde')).toBeNull()

  // A snapshot has no page number and the line is omitted, not printed empty.
  expect(screen.queryByText('Página')).toBeNull()
})

test('T-27: a corpus citation keeps document vocabulary and the in-force date', async () => {
  render(<CitationList citations={[CORPUS]} />)
  await userEvent.click(screen.getByRole('button', { name: /CG-AUTO-2024/ }))

  expect(screen.getByText('Documento')).toBeInTheDocument()
  expect(screen.getByText('Seção')).toBeInTheDocument()
  expect(screen.getByText('Vigente desde')).toBeInTheDocument()
  expect(screen.getByText('2024-01-01')).toBeInTheDocument()
  expect(screen.queryByText('Dados até')).toBeNull()
  expect(screen.getByText('p. 12')).toBeInTheDocument()
})

test('T-27: a superseded version says so, because a later one exists', async () => {
  render(<CitationList citations={[STALE]} />)
  await userEvent.click(screen.getByRole('button', { name: /NI-014/ }))

  expect(screen.getByText(/versão superada/i)).toBeInTheDocument()
})

test('T-27: a citation stored before provenance was recorded still renders', async () => {
  // source_kind is null on rows written before migration 0002. Null means "not
  // recorded", not "corpus" — but the panel still has to render, and it falls
  // back to document vocabulary rather than blanking the footer.
  const legacy: CitationOut = { ...CORPUS, source_kind: null }
  render(<CitationList citations={[legacy]} />)
  await userEvent.click(screen.getByRole('button', { name: /CG-AUTO-2024/ }))

  expect(screen.getByText('Documento')).toBeInTheDocument()
  expect(screen.getByText('Vigente desde')).toBeInTheDocument()
})
