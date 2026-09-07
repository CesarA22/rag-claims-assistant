import { useState } from 'react'
import type { CitationOut } from '../types'

export interface CitationListProps {
  citations: CitationOut[]
}

/** D-05's freshness line, in the words each source deserves.
 *
 *  A corpus citation is "vigente desde" its effective date: the version was in
 *  force from then until superseded. A database citation is a snapshot, so the
 *  same field means the opposite thing — "dados até", the point beyond which the
 *  data has nothing to say. Printing one label for both would misdescribe
 *  whichever it was not written for. */
function freshness(citation: CitationOut): { label: string; value: string } {
  if (citation.source_kind === 'claims') {
    return { label: 'Dados até', value: citation.effective_date }
  }
  return { label: 'Vigente desde', value: citation.effective_date }
}

function sourceLabel(citation: CitationOut): string {
  return citation.source_kind === 'claims'
    ? 'Banco de sinistros'
    : `${citation.document_title} (${citation.document_code})`
}

function pages(citation: CitationOut): string | null {
  // Null on anything read back from storage: the citations table has no page
  // columns. The line is omitted rather than printed empty.
  if (citation.page_from === null) return null
  if (citation.page_to === null || citation.page_to === citation.page_from) {
    return `p. ${citation.page_from}`
  }
  return `p. ${citation.page_from}–${citation.page_to}`
}

/** Numbered chips; clicking one opens the provenance panel beneath. */
export function CitationList({ citations }: CitationListProps) {
  const [open, setOpen] = useState<number | null>(null)
  if (citations.length === 0) return null
  const selected = open === null ? null : citations[open]

  return (
    <div className="mt-3">
      <div className="flex flex-wrap gap-2">
        {citations.map((citation, index) => (
          <button
            key={citation.evidence_id}
            type="button"
            onClick={() => setOpen(open === index ? null : index)}
            aria-expanded={open === index}
            className={`rounded border px-2 py-0.5 text-xs font-medium ${
              open === index
                ? 'border-zinc-800 bg-zinc-800 text-white'
                : 'border-zinc-400 bg-white text-zinc-700 hover:bg-zinc-100'
            }`}
          >
            [{index + 1}] {citation.source_kind === 'claims' ? 'Banco de sinistros' : citation.document_code}
          </button>
        ))}
      </div>

      {selected && (
        <div className="mt-2 rounded border border-zinc-300 bg-white p-3 text-sm text-zinc-800">
          <p className="mb-2 italic text-zinc-600">“{selected.snippet}”</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-zinc-500">
              {selected.source_kind === 'claims' ? 'Fonte' : 'Documento'}
            </dt>
            <dd>{sourceLabel(selected)}</dd>
            <dt className="text-zinc-500">
              {selected.source_kind === 'claims' ? 'Consulta' : 'Seção'}
            </dt>
            <dd>{selected.section}</dd>
            {pages(selected) && (
              <>
                <dt className="text-zinc-500">Página</dt>
                <dd>{pages(selected)}</dd>
              </>
            )}
            <dt className="text-zinc-500">Versão</dt>
            <dd>{selected.version}</dd>
            <dt className="text-zinc-500">{freshness(selected).label}</dt>
            <dd>{freshness(selected).value}</dd>
          </dl>
          {selected.superseded && (
            <p className="mt-2 rounded bg-amber-100 px-2 py-1 text-xs text-amber-900">
              Versão superada — existe uma versão posterior deste documento.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
