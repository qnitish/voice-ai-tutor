import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import type { Guest } from '@/lib/types'

interface GuestState {
  guest: Guest | null
  setGuest: (guest: Guest | null) => void
}

// localStorage can throw (private windows, blocked storage); fall back to memory only
const safeStorage = createJSONStorage(() => ({
  getItem: (k: string) => {
    try {
      return localStorage.getItem(k)
    } catch {
      return null
    }
  },
  setItem: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v)
    } catch {
      /* ignore */
    }
  },
  removeItem: (k: string) => {
    try {
      localStorage.removeItem(k)
    } catch {
      /* ignore */
    }
  },
}))

export const useGuestStore = create<GuestState>()(
  persist((set) => ({ guest: null, setGuest: (guest) => set({ guest }) }), {
    name: 'voice-tutor-guest',
    storage: safeStorage,
  }),
)
