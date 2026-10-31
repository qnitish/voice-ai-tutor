import { ArrowRight, BookOpenCheck, CirclePause, CircleHelp, Clock, ListChecks, Mic, Plus, RotateCcw, Smartphone, Volume2 } from 'lucide-react'
import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { type ReactNode, useEffect, useState } from 'react'

import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'

const EASE = [0.22, 1, 0.36, 1] as const

const PROBLEMS = [
  { title: 'Doubts get left behind', text: 'A recorded teacher can\'t hear you. The question stays open, and the next ten minutes get harder.' },
  { title: 'Rewinding repeats, it doesn\'t explain', text: 'Watching the same part again gives you the same words. Sometimes you need it said differently.' },
  { title: 'Feeling sure isn\'t knowing', text: 'It all makes sense while you watch. The gaps only show up later, in the test.' },
]

const STEPS = [
  { image: '/landing/lecture.jpg', alt: 'A handwritten lecture on motion under gravity playing in the video player', icon: CirclePause, title: 'Watch the real lecture', text: 'Your teacher\'s recorded class, on your phone or laptop, split into chapters.' },
  { image: '/landing/doubt.jpg', alt: 'A tutor\'s whiteboard answer titled "Why same rate of fall?" over the paused video', icon: Mic, title: 'Stuck? Just ask out loud', text: 'Tap the mic and speak. The video pauses, and the tutor explains on a whiteboard while it talks you through it.' },
  { image: '/landing/checkpoint.jpg', alt: 'A multiple choice question about a coin dropped from a moving lift, with a diagram', icon: ListChecks, title: 'Prove you got it', text: 'Questions come up at key moments. Get one wrong and you can try again or see the solution, then the lecture carries on.' },
]

const FEATURES = [
  { icon: Mic, title: 'Ask anytime, by voice', text: 'No typing. Tap to talk, or hold the mic for as long as you need.' },
  { icon: Clock, title: 'Knows what\'s coming next', text: 'If the lecture covers your doubt soon, the tutor can tell you and come back to it at the right moment.' },
  { icon: Volume2, title: 'Sounds like your teacher', text: 'Answers are spoken in a voice made from the lecture\'s own teacher.' },
  { icon: BookOpenCheck, title: 'Stays with your course', text: 'Answers build on the lecture you\'re watching and pages from its textbook.' },
  { icon: RotateCcw, title: 'Continue where you stopped', text: 'Your place, your answers and your doubts are all there when you come back.' },
  { icon: Smartphone, title: 'Made for your phone', text: 'Full-screen video with the mic always in reach of your thumb.' },
]

const FAQS = [
  { q: 'Do I need an account?', a: 'No. Enter your name and class and you can start right away. Your progress is kept on this device.' },
  { q: 'Why does it ask for my microphone?', a: 'So you can ask doubts out loud. We ask once when the lecture starts, so it won\'t interrupt you later. The mic only listens after you press it.' },
  { q: 'What if I say no to the mic?', a: 'The lecture and its questions still work. You just won\'t be able to ask doubts until you allow the mic in your browser settings.' },
  { q: 'What can I watch right now?', a: 'One full Physics lecture: Motion Under Gravity, in English, with 8 chapters.' },
  { q: 'Are the answers always correct?', a: 'The tutor is AI, so it can make mistakes. If something doesn\'t match your book or your teacher, trust them and check.' },
]

function Reveal({ children, delay = 0, className }: { children: ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-60px' }}
      transition={{ duration: 0.6, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="font-['Kalam'] text-lg font-bold text-amber-700">{children}</p>
}

/** The product in one loop: the lecture plays, the student asks, the board answers. All frames are real screenshots. */
function HeroDemo() {
  const [asking, setAsking] = useState(false)

  useEffect(() => {
    const id = window.setInterval(() => setAsking((a) => !a), 4500)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div className="relative">
      <div className="relative aspect-video overflow-hidden rounded-2xl border border-stone-900/10 bg-black shadow-2xl shadow-stone-900/20">
        <AnimatePresence initial={false}>
          <motion.img
            key={asking ? 'doubt' : 'lecture'}
            src={asking ? '/landing/doubt.jpg' : '/landing/lecture.jpg'}
            alt={asking ? 'The tutor answering a doubt on a whiteboard over the paused lecture' : 'A recorded physics lecture playing'}
            className="absolute inset-0 h-full w-full object-cover"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6 }}
          />
        </AnimatePresence>
      </div>
      {/* Under the video on a phone, where a floating bubble would hide the frame */}
      <div className="mt-3 flex items-center gap-3 sm:absolute sm:-bottom-6 sm:left-6 sm:mt-0 sm:max-w-sm">
        <span className={`flex size-11 shrink-0 items-center justify-center rounded-full text-white shadow-lg transition-colors duration-300 ${asking ? 'bg-red-600' : 'bg-stone-900'}`}>
          <Mic className="size-5" />
        </span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.p
            key={asking ? 'q' : 'hint'}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.3 }}
            className="rounded-2xl rounded-bl-sm border border-stone-900/10 bg-white px-4 py-2 text-sm leading-snug font-medium text-stone-900 shadow-lg"
          >
            {asking ? '"Why do a heavy stone and a light feather fall at the same rate?"' : 'Stuck on something? Just ask.'}
          </motion.p>
        </AnimatePresence>
      </div>
    </div>
  )
}

export function LandingPage({ onGetStarted }: { onGetStarted: () => void }) {
  const cta = (
    <Button size="lg" onClick={onGetStarted} className="h-12 rounded-full bg-stone-900 px-6 text-base text-white hover:bg-stone-800">
      Get started <ArrowRight className="size-4" />
    </Button>
  )

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-svh overflow-x-clip bg-[#fbf8f1] text-stone-900">
        <nav className="sticky top-0 z-30 border-b border-stone-900/5 bg-[#fbf8f1]/85 pt-[env(safe-area-inset-top)] backdrop-blur">
          <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-8">
            <Logo />
            <div className="flex items-center gap-1 sm:gap-6">
              <a href="#how" className="hidden text-sm text-stone-600 hover:text-stone-900 sm:block">How it works</a>
              <a href="#features" className="hidden text-sm text-stone-600 hover:text-stone-900 sm:block">Features</a>
              <a href="#faq" className="hidden text-sm text-stone-600 hover:text-stone-900 sm:block">FAQ</a>
              <Button size="sm" onClick={onGetStarted} className="rounded-full bg-stone-900 px-4 text-white hover:bg-stone-800">
                Get started
              </Button>
            </div>
          </div>
        </nav>

        <header className="mx-auto max-w-6xl px-4 pt-12 pb-16 sm:px-8 sm:pt-20 sm:pb-24">
          <motion.div className="mx-auto max-w-3xl text-center" initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease: EASE }}>
            <Eyebrow>For Class 6–12 students</Eyebrow>
            <h1 className="mt-3 text-5xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-7xl">
              Recorded lectures that <span className="relative whitespace-nowrap">answer back<svg aria-hidden="true" viewBox="0 0 300 12" preserveAspectRatio="none" className="absolute -bottom-2 left-0 h-2.5 w-full text-amber-400"><path d="M3 8c60-6 130-7 294-2" fill="none" stroke="currentColor" strokeWidth="5" strokeLinecap="round" /></svg></span>
            </h1>
            <p className="mx-auto mt-7 max-w-2xl text-lg leading-relaxed text-pretty text-stone-600 sm:text-xl">
              Watch your lecture and ask your doubt out loud the moment it comes up. The video pauses, an AI tutor explains on a whiteboard, and the class picks up right where you left it.
            </p>
            <div className="mt-8 flex flex-col items-center gap-3">
              {cta}
              <p className="text-sm text-stone-500">No sign-up. Just your name and class.</p>
            </div>
          </motion.div>
          <motion.div className="mx-auto mt-12 max-w-4xl sm:mt-16" initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, delay: 0.15, ease: EASE }}>
            <HeroDemo />
          </motion.div>
        </header>

        <section className="bg-stone-900 text-stone-50">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-8 sm:py-28">
            <Reveal className="max-w-2xl">
              <p className="font-['Kalam'] text-lg font-bold text-amber-400">The problem</p>
              <h2 className="mt-2 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">A video can't tell when you're lost.</h2>
            </Reveal>
            <div className="mt-12 grid gap-px overflow-hidden rounded-2xl bg-stone-700 md:grid-cols-3">
              {PROBLEMS.map((p, i) => (
                <Reveal key={p.title} delay={i * 0.08} className="bg-stone-900 p-6 sm:p-8">
                  <CircleHelp className="size-6 text-amber-400" />
                  <h3 className="mt-5 text-xl font-semibold">{p.title}</h3>
                  <p className="mt-2 leading-relaxed text-stone-400">{p.text}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="how" className="mx-auto max-w-6xl scroll-mt-16 px-4 py-20 sm:px-8 sm:py-28">
          <Reveal className="mx-auto max-w-2xl text-center">
            <Eyebrow>How it works</Eyebrow>
            <h2 className="mt-2 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">Like having your teacher pause the class for you.</h2>
          </Reveal>
          <div className="mt-14 flex flex-col gap-16 sm:mt-20 sm:gap-24">
            {STEPS.map((s, i) => (
              <div key={s.title} className="grid items-center gap-6 md:grid-cols-5 md:gap-12">
                <Reveal className={`md:col-span-3 ${i % 2 ? 'md:order-2' : ''}`}>
                  <img src={s.image} alt={s.alt} loading="lazy" className="aspect-video w-full rounded-2xl border border-stone-900/10 bg-black object-cover shadow-xl shadow-stone-900/10" />
                </Reveal>
                <Reveal delay={0.1} className="md:col-span-2">
                  <span className="flex size-11 items-center justify-center rounded-xl bg-amber-400 text-stone-950">
                    <s.icon className="size-5" />
                  </span>
                  <p className="mt-5 text-sm font-medium text-stone-500">Step {i + 1}</p>
                  <h3 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">{s.title}</h3>
                  <p className="mt-3 text-lg leading-relaxed text-stone-600">{s.text}</p>
                </Reveal>
              </div>
            ))}
          </div>
        </section>

        <section id="features" className="scroll-mt-16 border-y border-stone-900/10 bg-white">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-8 sm:py-28">
            <Reveal className="max-w-2xl">
              <Eyebrow>What you get</Eyebrow>
              <h2 className="mt-2 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">Built for the moment you get stuck.</h2>
            </Reveal>
            <div className="mt-12 grid gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((f, i) => (
                <Reveal key={f.title} delay={(i % 3) * 0.08}>
                  <f.icon className="size-6 text-amber-600" />
                  <h3 className="mt-4 text-lg font-semibold">{f.title}</h3>
                  <p className="mt-1.5 leading-relaxed text-stone-600">{f.text}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="mx-auto max-w-3xl scroll-mt-16 px-4 py-20 sm:px-8 sm:py-28">
          <Reveal>
            <h2 className="text-center text-4xl font-semibold tracking-tight sm:text-5xl">Questions</h2>
          </Reveal>
          <div className="mt-10 divide-y divide-stone-900/10 border-y border-stone-900/10">
            {FAQS.map((f) => (
              <details key={f.q} className="group">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-5 text-lg font-medium [&::-webkit-details-marker]:hidden">
                  {f.q}
                  <Plus className="size-5 shrink-0 text-stone-500 transition-transform group-open:rotate-45" />
                </summary>
                <p className="pb-5 leading-relaxed text-stone-600">{f.a}</p>
              </details>
            ))}
          </div>
        </section>

        <section className="px-4 pb-16 sm:px-8 sm:pb-24">
          <Reveal className="mx-auto max-w-6xl rounded-3xl bg-amber-400 px-6 py-16 text-center text-stone-950 sm:py-20">
            <h2 className="mx-auto max-w-2xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">Your next doubt doesn't have to wait.</h2>
            <p className="mt-4 text-lg">Start the Motion Under Gravity lecture now.</p>
            <div className="mt-8">{cta}</div>
          </Reveal>
        </section>

        <footer className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 pb-[max(env(safe-area-inset-bottom),2rem)] text-sm text-stone-500 sm:px-8">
          <Logo />
          <span>AI answers can make mistakes.</span>
        </footer>
      </div>
    </MotionConfig>
  )
}
