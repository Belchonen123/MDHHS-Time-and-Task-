import { Fragment, useMemo } from "react"
import { AnimatePresence, motion } from "framer-motion"

import { formatMoney } from "@/lib/format"
import { useReducedMotion } from "@/lib/useReducedMotion"
import { cn } from "@/lib/utils"

interface MoneyOdometerProps {
  value: number
  className?: string
}

/**
 * Per-digit roll animation for a currency figure.
 *
 * Renders each character in its own animated slot. When a character changes,
 * framer-motion's AnimatePresence cross-fades the new character up from below
 * while the old one slides out. Non-digit characters ($, ',', '.') get the
 * same slot so alignment stays perfect.
 *
 * Respects prefers-reduced-motion — falls back to a single static span.
 */
export function MoneyOdometer({ value, className }: MoneyOdometerProps) {
  const reduced = useReducedMotion()
  const str = useMemo(() => formatMoney(value), [value])

  if (reduced) {
    return <span className={cn("tabular", className)}>{str}</span>
  }

  return (
    <span
      className={cn("inline-flex tabular", className)}
      aria-label={str}
      role="text"
    >
      {Array.from(str).map((ch, idx) => (
        <DigitSlot key={idx} ch={ch} />
      ))}
    </span>
  )
}

function DigitSlot({ ch }: { ch: string }) {
  // Static (non-digit) characters render without the roll machinery so we
  // don't animate a '$' when the dollar amount changes.
  if (!/[0-9]/.test(ch)) {
    return <span aria-hidden>{ch}</span>
  }

  return (
    <span
      className="relative inline-block overflow-hidden text-center"
      style={{ width: "0.6em", height: "1em", lineHeight: 1 }}
      aria-hidden
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={ch}
          initial={{ y: "100%", opacity: 0 }}
          animate={{ y: "0%", opacity: 1 }}
          exit={{ y: "-100%", opacity: 0 }}
          transition={{
            y: { type: "spring", stiffness: 340, damping: 28 },
            opacity: { duration: 0.15 },
          }}
          className="absolute inset-0"
        >
          {ch}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

/**
 * Named export for Summary components that have mixed number types and want a
 * light wrapper without remounting.
 */
export function OdometerGroup({
  values,
  className,
}: {
  values: number[]
  className?: string
}) {
  return (
    <span className={className}>
      {values.map((v, i) => (
        <Fragment key={i}>
          {i > 0 && " / "}
          <MoneyOdometer value={v} />
        </Fragment>
      ))}
    </span>
  )
}

export default MoneyOdometer
