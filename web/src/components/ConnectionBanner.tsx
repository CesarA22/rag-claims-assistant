import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../api'

/** The banner exists so an open circuit is visible BEFORE the analyst types.
 *
 *  Three conditions, because "the API is unreachable" and "the API says it is
 *  degraded" are different problems with different fixes, and collapsing them
 *  would leave the more alarming one unlabelled. */
export function ConnectionBanner() {
  const { data, isError } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 5000,
    // Keep polling while the tab is unfocused. Without this the interval pauses
    // exactly when the analyst is away, so they return to a stale "all clear"
    // banner — the opposite of "an open circuit is visible BEFORE they type".
    refetchIntervalInBackground: true,
    retry: false,
  })

  if (isError) {
    return (
      <div className="border-b-2 border-red-600 bg-red-100 px-4 py-2 text-sm font-medium text-red-900">
        <span className="font-bold">Sem conexão com o serviço.</span> A interface
        continua disponível; nenhuma pergunta será enviada.
      </div>
    )
  }

  if (!data || data.status === 'ok') return null

  const dbDown = data.database.configured && data.database.reachable === false
  return (
    <div className="border-b-2 border-slate-500 bg-slate-200 px-4 py-2 text-sm text-slate-900">
      <span className="font-bold">Assistente indisponível.</span>{' '}
      {dbDown
        ? 'O banco de dados não está acessível, então nenhuma resposta pode ser registrada.'
        : `Circuito ${data.breaker.state}; as perguntas podem retornar apenas trechos das fontes.`}
      <span className="ml-2 font-mono text-xs text-slate-600">
        provider={data.provider} storage={data.storage}
      </span>
    </div>
  )
}
