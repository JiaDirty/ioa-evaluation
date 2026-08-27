import { useState, useEffect, useRef } from 'react'

export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)

    fetcher(controller.signal)
      .then(result => {
        if (!controller.signal.aborted) setData(result)
      })
      .catch(e => {
        if (!controller.signal.aborted && e.name !== 'AbortError') {
          setError(e.message)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => { controller.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const reload = () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)

    fetcher(controller.signal)
      .then(result => {
        if (!controller.signal.aborted) setData(result)
      })
      .catch(e => {
        if (!controller.signal.aborted && e.name !== 'AbortError') {
          setError(e.message)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
  }

  return { data, loading, error, reload }
}
