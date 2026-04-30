import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { useDraggable } from "@dnd-kit/core"
import { motion } from "framer-motion"
import { X as XIcon } from "lucide-react"

import { chipDragId } from "@/components/editor/dndIds"
import { cn } from "@/lib/utils"
import type { Placement } from "@/lib/scheduleEditor"

interface TaskChipProps {
  placement: Placement
  onChangeMinutes: (id: string, minutes: number) => void
  onRemove: (id: string) => void
  /** Presentational only — the orchestrator supplies this via activeId. */
  isGhost?: boolean
}

export function TaskChip({
  placement,
  onChangeMinutes,
  onRemove,
  isGhost,
}: TaskChipProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: chipDragId(placement),
    data: { source: "chip", placementId: placement.id },
  })

  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(placement.minutes))
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setValue(String(placement.minutes))
  }, [placement.minutes])

  useEffect(() => {
    if (editing) {
      const id = window.setTimeout(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      }, 0)
      return () => window.clearTimeout(id)
    }
  }, [editing])

  const commit = () => {
    const n = Number(value)
    if (!Number.isNaN(n) && n >= 0) {
      onChangeMinutes(placement.id, Math.round(n))
    }
    setEditing(false)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      commit()
    } else if (e.key === "Escape") {
      e.preventDefault()
      setValue(String(placement.minutes))
      setEditing(false)
    }
  }

  return (
    <motion.div
      layout
      layoutId={placement.id}
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: isDragging ? 0.4 : 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{
        layout: { type: "spring", stiffness: 380, damping: 32 },
        opacity: { duration: 0.14 },
      }}
      className={cn(
        "group relative flex items-center gap-2 rounded-md bg-white px-2 py-1.5 text-xs ring-1 ring-inset",
        isDragging || isGhost
          ? "ring-2 ring-primary-500 [ring-style:dashed]"
          : "ring-neutral-200",
        "shadow-xs",
      )}
    >
      {/* Drag handle — the whole chip is draggable except the minute pill and X */}
      <button
        type="button"
        ref={setNodeRef}
        {...attributes}
        {...listeners}
        aria-label={`Drag ${placement.taskName}`}
        className="flex min-w-0 flex-1 cursor-grab items-center gap-2 active:cursor-grabbing focus-visible:outline-none"
      >
        <span className="truncate font-medium text-neutral-900">
          {placement.taskName}
        </span>
      </button>

      {/* Minute value — click to edit */}
      {editing ? (
        <input
          ref={inputRef}
          type="number"
          min={0}
          step={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={onKeyDown}
          className={cn(
            "w-14 rounded-sm bg-white px-1 py-0.5 text-right font-mono text-[11px]",
            "ring-1 ring-inset ring-primary-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700",
          )}
        />
      ) : (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setEditing(true)
          }}
          title="Click to edit minutes"
          className={cn(
            "shrink-0 rounded-full bg-primary-50 px-2 py-0.5 font-semibold tabular text-primary-800",
            "hover:bg-primary-100",
          )}
        >
          {placement.minutes}m
        </button>
      )}

      {/* Remove — tiny X, visible on hover */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onRemove(placement.id)
        }}
        aria-label={`Remove ${placement.taskName}`}
        className={cn(
          "ml-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-neutral-300",
          "opacity-0 transition-opacity group-hover:opacity-100",
          "hover:bg-danger-bg hover:text-danger",
        )}
      >
        <XIcon className="h-3 w-3" strokeWidth={2.5} />
      </button>
    </motion.div>
  )
}

export default TaskChip
