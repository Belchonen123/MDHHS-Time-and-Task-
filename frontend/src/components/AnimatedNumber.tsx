import { useEffect, useRef, useState } from "react"

import { useReducedMotion } from "@/lib/useReducedMotion"

interface AnimatedNumberProps {
  /** Target value. */
  value: number
  /** Milliseconds to tween from the previous value to the new one. */
  durationMs?: number
  /** Formatter, e.g. `formatMoney` or `formatInt`. */
  format: (v: number) => string
  className?: string
}

/**
 * Tweens between numeric values so KPI displays don't jump.
 *
 * Prefers the framer-motion-free approach: a requestAnimationFrame loop with
 * an ease-out curve. Keeps bundle lean (avoids react-countup), and the caller
 * controls formatting so we don't accidentally lose currency formatting or
 * tabular alignment.
 *
 * Respects `prefers-reduced-motion` — skips the tween and shows the final
 * value immediately.
 */
export function AnimatedNumber({
  value,
  durationMs = 400,
  format,
  className,
}: AnimatedNumberProps) {
  const reduced = useReducedMotion()
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (reduced) {
      setDisplay(value)
      fromRef.current = value
      return
    }
    const from = fromRef.current
    const to = value
    if (from === to) return
    const start = performance.now()

    const tick = (now: number) => {
      const elapsed = now - start
      const t = Math.min(1, elapsed / durationMs)
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3)
      const v = from + (to - from) * eased
      setDisplay(v)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = to
        setDisplay(to)
      }
    }

    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(tick)

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [value, durationMs, reduced])

  return <span className={className}>{format(display)}</span>
}

export default AnimatedNumber
