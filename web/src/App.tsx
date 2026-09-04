import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Chat } from './components/Chat'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Chat />
    </QueryClientProvider>
  )
}
