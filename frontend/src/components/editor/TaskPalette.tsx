import { useDraggable, useDroppable } from "@dnd-kit/core"
import { motion } from "framer-motion"
import { AlertTriangle, Check, GripVertical, RotateCcw } from "lucide-react"

import { PALETTE_DROP_ID, paletteDragId } from "@/components/editor/dndIds"
import type { TaskCoverage, TaskMeta } from "@/lib/scheduleEditor"
import { cn } from "@/lib/utils"

interface TaskPaletteProps {
  tasks: readonly TaskMeta[]
  coverage: readonly TaskCoverage[]
  onReset: () => void
  /** Chip currently being dragged — used to show a faded source. */
  activeDragTaskName: string | null
}

function frequencyLabel(daysPerWeek: number): string {
  if (daysPerWeek === 7) return "daily"
  if (daysPerWeek <= 1) return "once a week"
  return `${daysPerWeek}× per week`
}

function PaletteTaskCard({
  task,
  coverage,
  isDraggedSource,
}: {
  task: TaskMeta
  coverage: TaskCoverage | undefined
  isDraggedSource: boolean
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: paletteDragId(task.name),
    data: { source: "palette", taskName: task.name },
  })

  const status = coverage?.status ?? "exact"

  return (
    <motion.div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      whileHover={{ y: -1 }}
      transition={{ duration: 0.12 }}
      className={cn(
        "group flex cursor-grab items-start gap-2 rounded-lg border bg-white p-3 shadow-xs",
        "active:cursor-grabbing active:shadow-lg",
        "transition-[border-color,box-shadow] duration-150",
        "border-neutral-200 hover:border-neutral-300",
        (isDragging || isDraggedSource) &&
          "scale-[1.03] border-primary-300 shadow-lg",
      )}
      style={{
        opacity: isDragging ? 0.4 : 1,
      }}
    >
      <GripVertical className="mt-0.5 h-4 w-4 shrink-0 text-neutral-300 group-hover:text-neutral-500" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <div className="truncate text-sm font-medium text-neutral-900">
            {task.name}
          </div>
          <span className="inline-flex shrink-0 items-center rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-800 tabular">
            {task.minPerDay}m
          </span>
        </div>
        <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[11px]">
          <span className="text-neutral-500">{frequencyLabel(task.daysPerWeek)}</span>
          {coverage && (
            <span
              className={cn(
                "inline-flex items-center gap-1 font-semibold tabular",
                status === "exact" && "text-success",
                status === "under" && "text-warning",
                status === "over" && "text-danger",
              )}
            >
              {coverage.placed}/{coverage.required}
              {status === "exact" ? (
                <Check className="h-3 w-3" strokeWidth={3} />
              ) : (
                <AlertTriangle className="h-3 w-3" strokeWidth={2.5} />
              )}
            </span>
          )}
        </div>
      </div>
    </motion.div>
  )
}

export function TaskPalette({
  tasks,
  coverage,
  onReset,
  activeDragTaskName,
}: TaskPaletteProps) {
  const coverageByName = new Map(coverage.map((c) => [c.name, c]))

  // The whole palette body is a droppable — dragging a chip back into it
  // removes it from the schedule.
  const { setNodeRef, isOver } = useDroppable({
    id: PALETTE_DROP_ID,
  })

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-neutral-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
        <div>
          <div className="label-caps text-[10px]">Task palette</div>
          <div className="text-sm text-neutral-600">
            {tasks.length} authorized task{tasks.length === 1 ? "" : "s"}
          </div>
        </div>
        <button
          type="button"
          onClick={onReset}
          className={cn(
            "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium",
            "text-neutral-600 transition-colors hover:bg-neutral-100 hover:text-neutral-900",
          )}
        >
          <RotateCcw className="h-3 w-3" />
          Reset
        </button>
      </div>

      {/* Body — droppable removal zone + draggable cards */}
      <div
        ref={setNodeRef}
        className={cn(
          "flex flex-1 flex-col gap-2 overflow-y-auto p-3 transition-colors",
          isOver && "bg-danger-bg",
        )}
      >
        {isOver && (
          <div className="mb-1 rounded-md border border-dashed border-danger px-2 py-2 text-center text-[11px] font-medium text-danger">
            Drop here to remove
          </div>
        )}
        {tasks.map((t) => (
          <PaletteTaskCard
            key={t.name}
            task={t}
            coverage={coverageByName.get(t.name)}
            isDraggedSource={activeDragTaskName === t.name}
          />
        ))}
      </div>
    </aside>
  )
}

export default TaskPalette
