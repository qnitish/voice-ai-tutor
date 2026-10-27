import { useState } from 'react'

import { Toaster } from '@/components/ui/sonner'
import type { LessonKind } from '@/lib/types'
import { HomePage } from '@/pages/HomePage'
import { LessonPage } from '@/pages/LessonPage'
import { VideoLessonPage } from '@/pages/VideoLessonPage'

export default function App() {
  const [open, setOpen] = useState<{ slug: string; kind: LessonKind } | null>(null)
  const exit = () => setOpen(null)

  return (
    <main className="min-h-svh bg-background px-4 py-6 sm:px-8">
      {open ? (
        open.kind === 'video' ? (
          <VideoLessonPage slug={open.slug} onExit={exit} />
        ) : (
          <LessonPage slug={open.slug} onExit={exit} />
        )
      ) : (
        <HomePage onStart={(slug, kind) => setOpen({ slug, kind })} />
      )}
      <Toaster position="top-center" />
    </main>
  )
}
