import { useEffect, useRef, useState } from 'react'

import { loadSkia, SkiaPainter } from '@/engine/skia'

/** The whiteboard painted with Skia (CanvasKit); loaded lazily so SVG lessons don't download it. */
export default function SkiaBoard() {
  const ref = useRef<HTMLCanvasElement>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | string>('loading')

  useEffect(() => {
    let painter: SkiaPainter | null = null
    let disposed = false
    loadSkia()
      .then((skia) => {
        if (disposed || !ref.current) return
        painter = new SkiaPainter(skia, ref.current)
        setStatus('ready')
      })
      .catch((e: unknown) => setStatus(`Skia failed to load: ${String(e)}`))
    return () => {
      disposed = true
      painter?.dispose()
    }
  }, [])

  return (
    <div className="relative h-full w-full">
      <canvas ref={ref} className="block h-full w-full" role="img" aria-label="Whiteboard" />
      {status !== 'ready' && (
        <p className="text-muted-foreground absolute inset-0 flex items-center justify-center p-4 text-center text-sm">
          {status === 'loading' ? 'Loading Skia…' : status}
        </p>
      )}
    </div>
  )
}
