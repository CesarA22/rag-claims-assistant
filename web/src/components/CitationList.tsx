import { useState } from 'react'
import type { CitationOut } from '../types'

export interface CitationListProps {
  citations: CitationOut[]
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
            [{index + 1}] {citation.document_code}
          </button>
        ))}
      </div>

      {selected && (
        <div className="mt-2 rounded border border-zinc-300 bg-white p-3 text-sm text-zinc-800">
          <p className="mb-2 italic text-zinc-600">“{selected.snippet}”</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-zinc-500">Documento</dt>
            <dd>
              {selected.document_title} ({selected.document_code})
            </dd>
            <dt className="text-zinc-500">Seção</dt>
            <dd>{selected.section}</dd>
            {pages(selected) && (
              <>
                <dt className="text-zinc-500">Página</dt>
                <dd>{pages(selected)}</dd>
              </>
            )}
            <dt className="text-zinc-500">Versão</dt>
            <dd>{selected.version}</dd>
            <dt className="text-zinc-500">Vigente desde</dt>
            <dd>{selected.effective_date}</dd>
          </dl>
        </div>
      )}
    </div>
  )
}
