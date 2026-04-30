import { useEffect, useState } from "react"

/**
 * Returns true when the user has set `prefers-reduced-motion: reduce`.
 *
 * Every component that drives its own animations (i.e. anything using
 * framer-motion directly, or a CSS transition with meaningful duration)
 * should check this hook and fall back to instant transitions.
 *
 * Framer Motion also provides its own `useReducedMotion`, but we wrap our
 * own so non-motion code (e.g. confetti, auto-scrolling) can respect the
 * same preference without pulling in framer as a dependency.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches
  })

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener?.("change", onChange)
    return () => mq.removeEventListener?.("change", onChange)
  }, [])

  return reduced
}
