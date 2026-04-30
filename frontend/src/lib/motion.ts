/**
 * Shared framer-motion presets.
 *
 * Every page transition, modal open, toast appearance, and state change
 * must use one of these. No bare CSS transitions — consistency is the polish.
 *
 * Durations and easings are kept in sync with `--duration-*` and
 * `--ease-out-soft` in `styles/tokens.css`.
 */

import type { Transition, Variants } from "framer-motion"

// Custom easing — matches var(--ease-out-soft). Feels more "material" than ease-out:
// fast start, luxurious settle.
export const easeOutSoft: [number, number, number, number] = [0.22, 1, 0.36, 1]

export const durations = {
  fast: 0.18,
  base: 0.24,
  slow: 0.4,
} as const

// ---------------------------------------------------------------------------
// Single-element presets (use as motion props: {...fadeIn} on a motion.div)
// ---------------------------------------------------------------------------

export const fadeIn = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: durations.fast, ease: "easeOut" } satisfies Transition,
}

export const slideUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: 8 },
  transition: { duration: durations.base, ease: easeOutSoft } satisfies Transition,
}

export const scaleIn = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.96 },
  transition: { duration: durations.fast, ease: easeOutSoft } satisfies Transition,
}

/**
 * successPulse — scale 1 → 1.04 → 1 over 400ms.
 * Used on "exact match" confirmations, validation-passed checkmarks, etc.
 */
export const successPulse = {
  initial: { scale: 1 },
  animate: { scale: [1, 1.04, 1] },
  transition: {
    duration: durations.slow,
    ease: easeOutSoft,
    times: [0, 0.5, 1],
  } satisfies Transition,
}

// ---------------------------------------------------------------------------
// Staggered list — parent + child variants
// ---------------------------------------------------------------------------

/**
 * listStagger — apply to the container; children get `listStaggerItem`.
 *
 *   <motion.ul variants={listStagger} initial="initial" animate="animate">
 *     {items.map(i => <motion.li key={i.id} variants={listStaggerItem}>…</motion.li>)}
 *   </motion.ul>
 */
export const listStagger: Variants = {
  initial: {},
  animate: {
    transition: {
      staggerChildren: 0.04, // 40ms
      delayChildren: 0.02,
    },
  },
  exit: {
    transition: {
      staggerChildren: 0.02,
      staggerDirection: -1,
    },
  },
}

export const listStaggerItem: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: durations.base, ease: easeOutSoft },
  },
  exit: {
    opacity: 0,
    y: 4,
    transition: { duration: durations.fast, ease: "easeOut" },
  },
}

// ---------------------------------------------------------------------------
// Page transition — default for route-level wrappers.
// ---------------------------------------------------------------------------

export const pageTransition = slideUp
