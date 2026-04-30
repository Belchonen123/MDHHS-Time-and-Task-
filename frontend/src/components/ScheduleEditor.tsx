import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core"
import { AnimatePresence, motion } from "framer-motion"
import {
  ArrowLeft,
  Check,
  FileCheck,
  Loader2,
  Undo2,
} from "lucide-react"
import { toast } from "sonner"

import { getClient, getPlan, updatePlan, validatePlan } from "@/api/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DayCard } from "@/components/editor/DayCard"
import { LiveValidationSidebar } from "@/components/editor/LiveValidationSidebar"
import { TaskPalette } from "@/components/editor/TaskPalette"
import { TaskChip } from "@/components/editor/TaskChip"
import { ValidateDialog } from "@/components/editor/ValidateDialog"
import {
  parseDragId,
  parseDropId,
  type DragSource,
} from "@/components/editor/dndIds"
import {
  addPlacement,
  authorizedMonthlyAmount,
  authorizedWeeklyMinutes,
  computeIssues,
  editorStateToApiSchedule,
  isPlaced,
  movePlacement,
  perTaskCoverage,
  placementsByDay,
  planToEditorState,
  removePlacement,
  setDayTime,
  setPlacementMinutes,
  statesEqual,
  taskMetaByName,
  taskMetaList,
  weeklyMinutes,
  type DayName,
  type EditorState,
  type Placement,
} from "@/lib/scheduleEditor"
import { WEEK_DAYS } from "@/lib/scheduleUtils"
import { cn } from "@/lib/utils"
import type { Client, Plan, ValidationReport } from "@/types"

const HARD_VARIANCE_PCT = 0.2 // 20% weekly-minute variance = hard-fail, disable save.
const HISTORY_CAP = 50
const UNDO_HINT_MS = 4000

export function ScheduleEditor() {
  const { id: clientId = "", version: versionStr = "" } = useParams<{
    id: string
    version: string
  }>()
  const navigate = useNavigate()
  const decodedId = useMemo(
    () => (clientId ? decodeURIComponent(clientId) : ""),
    [clientId],
  )
  const version = Math.max(0, Math.floor(Number.parseInt(versionStr, 10) || 0))

  const [client, setClient] = useState<Client | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [loadError, setLoadError] = useState(false)

  // Editor state + undo/redo stacks.
  // The stacks themselves are only read by the undo/redo setters (history &
  // future are prefixed with `_` so strict TS doesn't flag them as unused).
  const [state, setState] = useState<EditorState | null>(null)
  const initialStateRef = useRef<EditorState | null>(null)
  const [_history, setHistory] = useState<EditorState[]>([])
  const [_future, setFuture] = useState<EditorState[]>([])
  void _history
  void _future

  // Drag
  const [activeSource, setActiveSource] = useState<DragSource | null>(null)

  // Dialogs / async
  const [valOpen, setValOpen] = useState(false)
  const [valReport, setValReport] = useState<ValidationReport | null>(null)
  const [validating, setValidating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [saveStage, setSaveStage] = useState<"idle" | "saving" | "done">("idle")

  // UI hints — show "⌘Z to undo" once after the very first mutation.
  const [showUndoHint, setShowUndoHint] = useState(false)
  const undoHintShownRef = useRef(false)
  const undoHintTimer = useRef<number | null>(null)

  // -------------------------------------------------------------------------
  // Load plan
  // -------------------------------------------------------------------------
  const load = useCallback(async () => {
    if (!decodedId || !version) {
      setLoadError(true)
      return
    }
    setLoadError(false)
    try {
      const c = await getClient(decodedId)
      setClient(c.client)
      const found = c.plans.find((p) => p.version === version)
      const p = found ?? (await getPlan(decodedId, version))
      setPlan(p)
      const init = planToEditorState(p)
      initialStateRef.current = init
      setState(init)
      setHistory([])
      setFuture([])
    } catch {
      setLoadError(true)
    }
  }, [decodedId, version])

  useEffect(() => {
    void load()
  }, [load])

  // -------------------------------------------------------------------------
  // History-tracked mutation
  // -------------------------------------------------------------------------
  const mutate = useCallback(
    (updater: (prev: EditorState) => EditorState) => {
      setState((prev) => {
        if (!prev) return prev
        const next = updater(prev)
        if (statesEqual(prev, next)) return prev
        setHistory((h) => {
          const nh = [...h, prev]
          if (nh.length > HISTORY_CAP) nh.shift()
          return nh
        })
        setFuture([])
        if (!undoHintShownRef.current) {
          undoHintShownRef.current = true
          setShowUndoHint(true)
          if (undoHintTimer.current) window.clearTimeout(undoHintTimer.current)
          undoHintTimer.current = window.setTimeout(() => {
            setShowUndoHint(false)
          }, UNDO_HINT_MS)
        }
        return next
      })
    },
    [],
  )

  const undo = useCallback(() => {
    setHistory((h) => {
      if (h.length === 0) return h
      const prev = h[h.length - 1]
      setState((cur) => {
        if (cur) setFuture((f) => [cur, ...f].slice(0, HISTORY_CAP))
        return prev
      })
      return h.slice(0, -1)
    })
  }, [])

  const redo = useCallback(() => {
    setFuture((f) => {
      if (f.length === 0) return f
      const next = f[0]
      setState((cur) => {
        if (cur) setHistory((h) => [...h, cur].slice(-HISTORY_CAP))
        return next
      })
      return f.slice(1)
    })
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey
      if (!mod) return
      const key = e.key.toLowerCase()
      // Don't hijack while typing in an input/textarea
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return
      }
      if (key === "z" && !e.shiftKey) {
        e.preventDefault()
        undo()
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [undo, redo])

  useEffect(() => {
    return () => {
      if (undoHintTimer.current) window.clearTimeout(undoHintTimer.current)
    }
  }, [])

  // -------------------------------------------------------------------------
  // Derived
  // -------------------------------------------------------------------------
  const tasks = plan?.tasks ?? []
  const targetWeekly = useMemo(() => authorizedWeeklyMinutes(tasks), [tasks])
  const authorizedAmount = useMemo(
    () => authorizedMonthlyAmount(tasks),
    [tasks],
  )
  const metaList = useMemo(() => taskMetaList(tasks), [tasks])
  const metaByName = useMemo(() => taskMetaByName(tasks), [tasks])

  const derived = useMemo(() => {
    if (!state || !client) return null
    const weekly = weeklyMinutes(state)
    const api = editorStateToApiSchedule(state, client.pay_rate, tasks)
    const coverage = perTaskCoverage(state, tasks)
    const issues = computeIssues(state, tasks)
    return { weekly, api, coverage, issues }
  }, [state, client, tasks])

  const isDirty = useMemo(
    () =>
      state && initialStateRef.current
        ? !statesEqual(state, initialStateRef.current)
        : false,
    [state],
  )

  const hardFail = useMemo(() => {
    if (!derived) return false
    if (targetWeekly === 0) return false
    const diff = Math.abs(derived.weekly - targetWeekly) / targetWeekly
    return diff > HARD_VARIANCE_PCT
  }, [derived, targetWeekly])

  // -------------------------------------------------------------------------
  // Mutation helpers wired to UI
  // -------------------------------------------------------------------------
  const onChangeTime = useCallback(
    (day: DayName, key: "start" | "end", value: string) => {
      mutate((prev) => setDayTime(prev, day, key, value))
    },
    [mutate],
  )

  const onChangeMinutes = useCallback(
    (placementId: string, minutes: number) => {
      mutate((prev) => setPlacementMinutes(prev, placementId, minutes))
    },
    [mutate],
  )

  const onRemovePlacement = useCallback(
    (placementId: string) => {
      mutate((prev) => removePlacement(prev, placementId))
    },
    [mutate],
  )

  const onResetToCanonical = useCallback(() => {
    if (!initialStateRef.current) return
    const init = initialStateRef.current
    setState((prev) => {
      if (prev && !statesEqual(prev, init)) {
        setHistory((h) => [...h, prev].slice(-HISTORY_CAP))
        setFuture([])
      }
      return init
    })
    setResetOpen(false)
  }, [])

  // -------------------------------------------------------------------------
  // Drag handlers
  // -------------------------------------------------------------------------
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 4 },
    }),
  )

  const onDragStart = useCallback((ev: DragStartEvent) => {
    const src = parseDragId(ev.active.id)
    setActiveSource(src)
  }, [])

  const onDragCancel = useCallback(() => {
    setActiveSource(null)
  }, [])

  const onDragEnd = useCallback(
    (ev: DragEndEvent) => {
      const src = parseDragId(ev.active.id)
      const tgt = parseDropId(ev.over?.id ?? null)
      setActiveSource(null)
      if (!src || !state) return

      if (src.kind === "palette") {
        if (!tgt || tgt.kind !== "day") return
        const meta = metaByName.get(src.taskName)
        if (!meta) return
        if (isPlaced(state, tgt.day, src.taskName)) {
          toast.error(`${src.taskName} is already on ${tgt.day}`, {
            duration: 2200,
          })
          return
        }
        mutate((prev) =>
          addPlacement(prev, tgt.day, src.taskName, meta.minPerDay),
        )
        return
      }

      if (src.kind === "chip") {
        const chip = state.placements.find((p) => p.id === src.placementId)
        if (!chip) return

        if (!tgt) return
        if (tgt.kind === "palette") {
          mutate((prev) => removePlacement(prev, src.placementId))
          return
        }
        // tgt.kind === 'day'
        if (chip.day === tgt.day) return
        if (isPlaced(state, tgt.day, chip.taskName)) {
          toast.error(`${chip.taskName} is already on ${tgt.day}`, {
            duration: 2200,
          })
          return
        }
        mutate((prev) => movePlacement(prev, src.placementId, tgt.day))
      }
    },
    [state, metaByName, mutate],
  )

  // -------------------------------------------------------------------------
  // Top-bar actions
  // -------------------------------------------------------------------------
  const onCancelClick = () => {
    if (isDirty) setCancelOpen(true)
    else navigate(`/clients/${encodeURIComponent(decodedId)}`)
  }

  const onConfirmCancel = () => {
    setCancelOpen(false)
    navigate(`/clients/${encodeURIComponent(decodedId)}`)
  }

  const onValidate = async () => {
    if (!derived || !decodedId) return
    setValidating(true)
    setValReport(null)
    setValOpen(true)
    try {
      const r = await validatePlan(
        decodedId,
        version,
        derived.api as unknown as Record<string, unknown>,
      )
      setValReport(r)
    } catch {
      setValOpen(false)
    } finally {
      setValidating(false)
    }
  }

  const onSave = useCallback(async () => {
    if (!derived || !decodedId) return
    setSaving(true)
    setSaveStage("saving")
    try {
      await updatePlan(decodedId, version, {
        schedule: derived.api as unknown as Record<string, unknown>,
      })
      setSaveStage("done")
      await new Promise((r) => setTimeout(r, 600))
      toast.success("Plan updated and output files rebuilt", { duration: 3600 })
      navigate(`/clients/${encodeURIComponent(decodedId)}`)
    } catch {
      setSaveStage("idle")
    } finally {
      setSaving(false)
    }
  }, [derived, decodedId, version, navigate])

  // Called from inside the Validate dialog ("Save anyway")
  const onSaveFromValidate = async () => {
    setValOpen(false)
    await onSave()
  }

  // -------------------------------------------------------------------------
  // Early-return states
  // -------------------------------------------------------------------------
  if (!decodedId || !version) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-6">
        <p className="text-sm text-neutral-600">Invalid link.</p>
      </div>
    )
  }
  if (loadError) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6">
        <p className="text-danger">Could not load this plan for editing.</p>
        <Button type="button" variant="outline" asChild>
          <Link to={`/clients/${encodeURIComponent(decodedId)}`}>
            <ArrowLeft className="mr-1 h-4 w-4" /> Back to client
          </Link>
        </Button>
      </div>
    )
  }
  if (!plan || !client || !state || !derived) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-neutral-400" />
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Active drag preview (for DragOverlay)
  // -------------------------------------------------------------------------
  const activeChip: Placement | null =
    activeSource?.kind === "chip"
      ? state.placements.find((p) => p.id === activeSource.placementId) ?? null
      : null

  const activePaletteTaskName =
    activeSource?.kind === "palette" ? activeSource.taskName : null

  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={onDragCancel}
    >
      <div className="flex h-dvh flex-col bg-neutral-50">
        {/* ===== Top bar ===== */}
        <header className="flex h-[64px] shrink-0 items-center justify-between border-b border-neutral-200 bg-white px-6 shadow-xs">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={onCancelClick}
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                "text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-900",
              )}
              aria-label="Back"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div className="min-w-0">
              <div className="label-caps text-[10px] text-primary-600">
                Editing plan
              </div>
              <h1 className="truncate font-display text-lg font-semibold tracking-tight text-neutral-900">
                Plan v{version} · {client.client_name}
              </h1>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <AnimatePresence>
              {showUndoHint && (
                <motion.span
                  initial={{ opacity: 0, y: -2 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="mr-1 inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2.5 py-1 text-[11px] font-medium text-neutral-600"
                >
                  <Undo2 className="h-3 w-3" />
                  <kbd className="font-mono">⌘Z</kbd> to undo
                </motion.span>
              )}
            </AnimatePresence>

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onCancelClick}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void onValidate()}
              disabled={validating}
            >
              {validating ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <FileCheck className="mr-1 h-3 w-3" />
              )}
              Validate
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={() => void onSave()}
              disabled={saving || hardFail}
              className="min-w-[150px] bg-primary-700 text-white shadow-sm hover:bg-primary-800 hover:shadow-md"
            >
              {saveStage === "saving" ? (
                <span className="inline-flex items-center gap-1.5">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Saving…
                </span>
              ) : saveStage === "done" ? (
                <span className="inline-flex items-center gap-1.5">
                  <Check className="h-3 w-3" strokeWidth={3} />
                  Saved
                </span>
              ) : (
                "Save & Rebuild"
              )}
            </Button>
          </div>
        </header>

        {/* ===== Three columns ===== */}
        <div className="flex min-h-0 flex-1">
          <TaskPalette
            tasks={metaList}
            coverage={derived.coverage}
            onReset={() => setResetOpen(true)}
            activeDragTaskName={activePaletteTaskName}
          />

          <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
            {hardFail && (
              <div className="border-b border-danger/20 bg-danger-bg px-4 py-2 text-xs font-medium text-danger">
                Weekly minutes are far off target ({derived.weekly} vs{" "}
                {targetWeekly}). Saving is disabled until this is corrected.
              </div>
            )}
            <div className="flex-1 overflow-auto p-4">
              <div className="grid min-w-[760px] grid-cols-7 gap-3">
                {WEEK_DAYS.map((d) => {
                  const day = d as DayName
                  const placements = placementsByDay(state)[day] as Placement[]
                  const invalid = activeSource
                    ? isTargetInvalid(state, activeSource, day)
                    : false
                  return (
                    <DayCard
                      key={day}
                      day={day}
                      placements={placements}
                      time={state.times[day]}
                      isValidDropTarget={!!activeSource && !invalid}
                      isInvalidDropTarget={!!activeSource && invalid}
                      onChangeTime={onChangeTime}
                      onChangeMinutes={onChangeMinutes}
                      onRemovePlacement={onRemovePlacement}
                    />
                  )
                })}
              </div>
            </div>
          </main>

          <LiveValidationSidebar
            weeklyMinutes={derived.weekly}
            targetWeeklyMinutes={targetWeekly}
            currentMonthlyAmount={derived.api.monthly_amount}
            authorizedMonthlyAmount={authorizedAmount}
            taskCoverage={derived.coverage}
            issues={derived.issues}
          />
        </div>
      </div>

      {/* ===== Drag overlay (floating preview) ===== */}
      <DragOverlay dropAnimation={{ duration: 220 }}>
        {activeChip ? (
          <div className="pointer-events-none">
            <TaskChip
              placement={activeChip}
              onChangeMinutes={() => undefined}
              onRemove={() => undefined}
              isGhost
            />
          </div>
        ) : activePaletteTaskName ? (
          <PalettePreview
            taskName={activePaletteTaskName}
            minutes={metaByName.get(activePaletteTaskName)?.minPerDay ?? 0}
          />
        ) : null}
      </DragOverlay>

      {/* ===== Dialogs ===== */}
      <ValidateDialog
        open={valOpen}
        onOpenChange={setValOpen}
        report={valReport}
        validating={validating}
        onSaveAnyway={() => void onSaveFromValidate()}
        onKeepEditing={() => setValOpen(false)}
      />

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Discard unsaved changes?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-neutral-600">
            Your edits to this schedule will be lost. This can&apos;t be undone.
          </p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setCancelOpen(false)}
            >
              Keep editing
            </Button>
            <Button
              type="button"
              onClick={onConfirmCancel}
              className="bg-danger hover:bg-danger/90"
            >
              Discard changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Reset to canonical schedule?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-neutral-600">
            Restores the originally generated schedule for plan v{version}.
            Your current edits will be cleared (you can still undo after).
          </p>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setResetOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={onResetToCanonical}
              className="bg-primary-700 hover:bg-primary-800"
            >
              Reset schedule
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DndContext>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isTargetInvalid(
  state: EditorState,
  source: DragSource,
  day: DayName,
): boolean {
  if (source.kind === "palette") {
    return isPlaced(state, day, source.taskName)
  }
  const chip = state.placements.find((p) => p.id === source.placementId)
  if (!chip) return false
  if (chip.day === day) return false
  return isPlaced(state, day, chip.taskName)
}

function PalettePreview({
  taskName,
  minutes,
}: {
  taskName: string
  minutes: number
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-primary-300 bg-white px-3 py-2 shadow-lg">
      <span className="text-sm font-medium text-neutral-900">{taskName}</span>
      <span className="shrink-0 rounded-full bg-primary-50 px-2 py-0.5 text-[11px] font-semibold tabular text-primary-800">
        {minutes}m
      </span>
    </div>
  )
}

export default ScheduleEditor

