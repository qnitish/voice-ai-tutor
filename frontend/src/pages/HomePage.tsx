import { BookOpen, Play } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'
import type { LessonSummary } from '@/lib/types'
import { useGuestStore } from '@/store/guest'

function GuestForm() {
  const setGuest = useGuestStore((s) => s.setGuest)
  const [name, setName] = useState('')
  const [classLevel, setClassLevel] = useState('12')
  const [language, setLanguage] = useState('hinglish')
  const [saving, setSaving] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      setGuest(await api.createGuest({ name: name.trim(), class_level: Number(classLevel), language }))
    } catch {
      toast.error('Could not save. Is the backend running?')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="mx-auto w-full max-w-md">
      <form onSubmit={submit}>
        <CardHeader>
          <CardTitle>Namaste! 👋</CardTitle>
          <CardDescription>Tell us a little about you to start learning. No sign-up needed.</CardDescription>
        </CardHeader>
        <CardContent className="mt-4 grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label>Class</Label>
              <Select value={classLevel} onValueChange={setClassLevel}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[6, 7, 8, 9, 10, 11, 12].map((c) => (
                    <SelectItem key={c} value={String(c)}>
                      Class {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="hinglish">Hinglish</SelectItem>
                  <SelectItem value="hindi">Hindi</SelectItem>
                  <SelectItem value="english">English</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
        <CardFooter className="mt-6">
          <Button type="submit" className="w-full" disabled={saving || !name.trim()}>
            Continue
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}

export function HomePage({ onStart }: { onStart: (slug: string) => void }) {
  const { guest, setGuest } = useGuestStore()
  const [lessons, setLessons] = useState<LessonSummary[] | null>(null)

  useEffect(() => {
    api.listLessons().then(setLessons, () => setLessons([]))
  }, [])

  // Drop a stale guest id (e.g. after the database was reset)
  useEffect(() => {
    if (guest) api.getGuest(guest.id).then(setGuest, () => setGuest(null))
  }, [guest?.id, setGuest])

  if (!guest) return <GuestForm />

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Hi {guest.name}!</h1>
          <p className="text-muted-foreground text-sm">Pick a lesson. You can interrupt the tutor anytime with the mic.</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setGuest(null)}>
          Not {guest.name}?
        </Button>
      </div>
      {lessons === null && <p className="text-muted-foreground text-sm">Loading lessons…</p>}
      {lessons?.length === 0 && (
        <p className="text-muted-foreground text-sm">
          No lessons yet. Run <code>python -m app.prep</code> in the backend to create one.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {lessons?.map((l) => (
          <Card key={l.slug}>
            <CardHeader>
              <BookOpen className="text-muted-foreground mb-2 size-5" />
              <CardTitle className="leading-snug">{l.title}</CardTitle>
              <CardDescription>{l.scene_count} scenes</CardDescription>
            </CardHeader>
            <CardFooter className="mt-4">
              <Button onClick={() => onStart(l.slug)}>
                <Play className="size-4" /> Start lesson
              </Button>
            </CardFooter>
          </Card>
        ))}
      </div>
    </div>
  )
}
