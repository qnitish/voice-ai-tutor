import { useState } from 'react'

import { Toaster } from '@/components/ui/sonner'
import { HomePage } from '@/pages/HomePage'
import { LessonPage } from '@/pages/LessonPage'

export default function App() {
  const [lessonSlug, setLessonSlug] = useState<string | null>(null)

  return (
    <main className="min-h-svh bg-background px-4 py-6 sm:px-8">
      {lessonSlug ? (
        <LessonPage slug={lessonSlug} onExit={() => setLessonSlug(null)} />
      ) : (
        <HomePage onStart={setLessonSlug} />
      )}
      <Toaster position="top-center" />
    </main>
  )
}
