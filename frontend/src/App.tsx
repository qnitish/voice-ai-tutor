import { useState } from 'react'

import { Toaster } from '@/components/ui/sonner'
import type { LessonKind } from '@/lib/types'
import { HomePage } from '@/pages/HomePage'
import { LandingPage } from '@/pages/LandingPage'
import { LessonPage } from '@/pages/LessonPage'
import { VideoLessonPage } from '@/pages/VideoLessonPage'
import { useGuestStore } from '@/store/guest'

export default function App() {
  const [open, setOpen] = useState<{ slug: string; kind: LessonKind } | null>(null)
  const exit = () => setOpen(null)
  const hasGuest = useGuestStore((s) => !!s.guest)
  // Get Started leads to the name and class form; a returning student goes straight to their lectures
  const [signingUp, setSigningUp] = useState(false)

  if (!open && !hasGuest && !signingUp) {
    return (
      <>
        <LandingPage onGetStarted={() => setSigningUp(true)} />
        <Toaster position="top-center" />
      </>
    )
  }

  return (
    <main className="min-h-svh bg-background px-3 pt-[max(env(safe-area-inset-top),1rem)] pb-6 sm:px-8 sm:pt-6">
      {open ? (
        open.kind === 'video' ? (
          <VideoLessonPage slug={open.slug} onExit={exit} />
        ) : (
          <LessonPage slug={open.slug} onExit={exit} />
        )
      ) : (
        <HomePage onStart={(slug, kind) => setOpen({ slug, kind })} onBack={() => setSigningUp(false)} />
      )}
      <Toaster position="top-center" />
    </main>
  )
}
