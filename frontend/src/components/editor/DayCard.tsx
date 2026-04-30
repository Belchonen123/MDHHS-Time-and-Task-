import { useDroppable } from "@dnd-kit/core"
import { AnimatePresence, motion } from "framer-motion"
import { Moon, Sparkles } from "lucide-react"

import { dayDropId } from "@/components/editor/dndIds"
import { TaskChip } from "@/components/editor/TaskChip"
import { getShortDow, isFridayDay, isWeekendDay } from "@/lib/scheduleUtils"
import { formatHoursMinutes, formatInt } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { DayName, Placement } from "@/lib/scheduleEditor"

interface DayCardProps {
  day: DayName
  placements: readonly Placement[]
  time: { start: string; end: string }
  /** Whether this card is the valid drop target for the current drag. */
  isValidDropTarget: boolean
  /** Whether this card is an invalid drop target (already has the dragged task). */
  isInvalidDropTarget: boolean
  onChangeTime: (day: DayName, key: "start" | "end", value: string) => void
  onChangeMinutes: (placementId: string, minutes: number) => void
  onRemovePlacement: (placementId: string) => void
}

export function DayCard({
  day,
  placements,
  time,
  isValidDropTarget,
  isInvalidDropTarget,
  onChangeTime,
  onChangeMinutes,
  onRemovePlacement,
}: DayCardProps) {
  const { setNodeRef, isOver } = useDroppable({
    id: dayDropId(day),
    data: { day },
  })

  const totalMinutes = placements.reduce((s, p) => s + p.minutes, 0)
  const weekend = isWeekendDay(day)
  const hwDay = isFridayDay(day)
  const empty = placements.length === 0

  const highlight = isOver && isValidDropTarget
  const reject = isOver && isInvalidDropTarget

  return (
    <motion.section
      ref={setNodeRef}
      layout
      transition={{ layout: { type: "spring", stiffness: 380, damping: 32 } }}
      className={cn(
        "relative flex min-w-0 flex-col overflow-hidden rounded-lg border bg-neutral-50 shadow-xs",
        weekend && "bg-[color:#FFF4EC]",
        hwDay && !weekend && "bg-amber-50/60",
        highlight &&
          "border-primary-500 bg-primary-50 ring-2 ring-primary-300/50",
        reject && "border-danger bg-danger-bg",
        !highlight && !reject && "border-neutral-200",
      )}
    >
      {/* Header */}
      <header className="flex items-baseline justify-between gap-2 border-b border-neutral-200/80 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <h3 className="font-display text-sm font-semibold text-neutral-900">
            {getShortDow(day)}
          </h3>
          {hwDay && (
            <Sparkles
              className="h-3 w-3 text-amber-600"
              aria-label="Deviation day"
            />
          )}
        </div>
        <span className="tabular text-[11px] font-medium text-neutral-600">
          {totalMinutes > 0 ? `${formatInt(totalMinutes)}m` : "—"}
        </span>
      </header>

      {/* Chips (with empty state) */}
      <div
        className={cn(
          "flex min-h-[90px] flex-1 flex-col gap-1.5 p-2",
          reject && "cursor-not-allowed",
        )}
      >
        <AnimatePresence initial={false}>
          {empty ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className={cn(
                "flex flex-1 flex-col items-center justify-center gap-1 rounded-md",
                "border border-dashed border-neutral-300 text-neutral-400",
                highlight && "border-primary-500 text-primary-700",
                reject && "border-danger text-danger",
              )}
            >
              <Moon className="h-4 w-4" />
              <span className="text-[11px] font-medium">
                {highlight ? "Drop here" : reject ? "Already here" : "Rest day"}
              </span>
            </motion.div>
          ) : (
            placements.map((p) => (
              <TaskChip
                key={p.id}
                placement={p}
                onChangeMinutes={onChangeMinutes}
                onRemove={onRemovePlacement}
              />
            ))
          )}
        </AnimatePresence>
      </div>

      {/* Footer — times + duration */}
      <footer className="border-t border-neutral-200/80 bg-white/60 px-3 py-2">
        <div className="grid grid-cols-2 gap-1.5">
          <TimeInput
            label="In"
            value={time.start}
            onChange={(v) => onChangeTime(day, "start", v)}
          />
          <TimeInput
            label="Out"
            value={time.end}
            onChange={(v) => onChangeTime(day, "end", v)}
          />
        </div>
        <div className="mt-1.5 flex items-baseline justify-between text-[11px]">
          <span className="label-caps text-[9px]">Dur</span>
          <span className="tabular font-medium text-neutral-700">
            {totalMinutes > 0 ? formatHoursMinutes(totalMinutes) : "—"}
          </span>
        </div>
      </footer>
    </motion.section>
  )
}

function TimeInput({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="label-caps text-[9px]">{label}</span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "w-full rounded-sm bg-white px-1.5 py-1 font-mono text-[11px] text-neutral-900",
          "ring-1 ring-inset ring-neutral-200 transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700",
        )}
      />
    </label>
  )
}

export default DayCard
