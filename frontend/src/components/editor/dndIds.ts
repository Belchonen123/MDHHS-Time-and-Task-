/**
 * Central registry of all dnd-kit ids used by the editor.
 *
 * Keeping them here (instead of building strings at each call site) means
 * the droppable id a `DayCard` registers and the target id the orchestrator
 * reads from `onDragEnd` can never drift apart.
 */

import type { DayName, Placement } from "@/lib/scheduleEditor"

export const PALETTE_DROP_ID = "palette:dropzone"

/** A card sitting in the left palette (source — drags ADD new placements). */
export function paletteDragId(taskName: string): string {
  return `palette:${taskName}`
}

/** A drop zone for a specific weekday. */
export function dayDropId(day: DayName): string {
  return `day:${day}`
}

/** A placed chip inside a day card (can be moved or removed). */
export function chipDragId(p: Placement): string {
  return `chip:${p.id}`
}

// ---------------------------------------------------------------------------
// Type-safe parsers for onDragEnd
// ---------------------------------------------------------------------------

export type DragSource =
  | { kind: "palette"; taskName: string }
  | { kind: "chip"; placementId: string }

export type DragTarget =
  | { kind: "day"; day: DayName }
  | { kind: "palette" }
  | null

export function parseDragId(id: unknown): DragSource | null {
  if (typeof id !== "string") return null
  if (id.startsWith("palette:")) {
    const taskName = id.slice("palette:".length)
    return taskName ? { kind: "palette", taskName } : null
  }
  if (id.startsWith("chip:")) {
    const placementId = id.slice("chip:".length)
    return placementId ? { kind: "chip", placementId } : null
  }
  return null
}

export function parseDropId(id: unknown): DragTarget {
  if (typeof id !== "string") return null
  if (id === PALETTE_DROP_ID) return { kind: "palette" }
  if (id.startsWith("day:")) {
    const day = id.slice("day:".length) as DayName
    return { kind: "day", day }
  }
  return null
}
