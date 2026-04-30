import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react"
import { useNavigate } from "react-router-dom"
import { AnimatePresence, motion } from "framer-motion"
import {
  AlertCircle,
  Check,
  FileUp,
  Loader2,
  Upload,
  X as XIcon,
} from "lucide-react"
import { toast } from "sonner"

import { ApiError, isDayCapacityDetail, uploadPDF } from "@/api/client"
import { DayCapacityErrorPanel } from "@/components/DayCapacityErrorPanel"
import { WorkerAvailabilitySection } from "@/components/upload/WorkerAvailabilitySection"
import { Button } from "@/components/ui/button"
import {
  MonthYearPicker,
  currentMonthYear,
  formatMonthYear,
  type MonthYear,
} from "@/components/upload/MonthYearPicker"
import {
  firstAvailabilityPreflightViolation,
  visitWeekdaysUnionFromAuthorizedTasks,
} from "@/lib/scheduleUtils"
import { defaultWorkerAvailability } from "@/lib/workerAvailability"
import { cn } from "@/lib/utils"
import { easeOutSoft } from "@/lib/motion"
import type { DayCapacityDetail, UploadResult } from "@/types"

// ===========================================================================
// Config
// ===========================================================================

const ACCEPT = "application/pdf"
const STAGE_DURATION_MS = 1500

const STAGES = [
  "Extracting tasks from PDF",
  "Calculating schedule",
  "Running cross-checks",
  "Building workbook & PDF",
] as const

type StageIndex = 0 | 1 | 2 | 3

type Phase =
  | { kind: "idle" }
  | { kind: "processing"; stage: StageIndex; allDone: boolean }
  | { kind: "flash" } // brief green flash on all checks before navigation
  | {
      kind: "parseError"
      message: string
      title?: string
      dayCapacity?: DayCapacityDetail
    }

type DragState = "none" | "over" | "invalid"

type UploadHeroProps = {
  /** Called after a successful upload so the parent can refresh its list. */
  onUploaded?: (result: UploadResult) => void
  /** Compact variant — a slim 100px strip (shown once the user has any clients). */
  variant?: "hero" | "compact"
  /**
   * When set, the ≥120 min too-narrow preflight only applies to weekdays that
   * appear in this union (from PDF task `days_per_week`). If omitted, every
   * weekday is checked (matches backend assert_worker_availability_sane).
   */
  authorizedTasksForPreflight?: ReadonlyArray<{ days_per_week: number }>
}

// ===========================================================================
// Tiny helpers
// ===========================================================================

function sleep(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms))
}

function isPdfFile(f: File | DataTransferItem): boolean {
  const name = "name" in f ? f.name.toLowerCase() : ""
  const type = f.type
  return type === ACCEPT || name.endsWith(".pdf")
}

function dragHasPdf(e: ReactDragEvent<HTMLElement>): boolean {
  const items = e.dataTransfer?.items
  if (!items || items.length === 0) return true // unknown — be permissive until drop
  for (let i = 0; i < items.length; i++) {
    if (items[i].kind !== "file") continue
    if (items[i].type === ACCEPT) return true
    // Chrome reports "" for some .pdf files during dragover — only reject on concrete mismatch.
    if (items[i].type && items[i].type !== ACCEPT) return false
  }
  return true
}

/** Browser `fetch` failures (backend down, wrong origin, CORS, offline). */
function isLikelyNetworkError(message: string): boolean {
  const m = message.trim().toLowerCase()
  return (
    m.includes("failed to fetch") ||
    m.includes("networkerror") ||
    m.includes("network request failed") ||
    m.includes("load failed") ||
    m.includes("ecconrefused") ||
    m.includes("connection refused") ||
    m.includes("err_connection_refused")
  )
}

/**
 * HTTP 400 from upload after the PDF was already parsed — schedule / availability,
 * not a PDF read failure. Keep in sync with backend CalibrationError messages.
 */
function isScheduleBuildErrorMessage(msg: string): boolean {
  return (
    /this day'?s visits need/i.test(msg) ||
    /minutes fit between/i.test(msg) ||
    /scheduled visits on this day total/i.test(msg) ||
    /worker availability for that weekday only allows/i.test(msg) ||
    /worker availability only allows \d+ minutes on that weekday/i.test(msg) ||
    /could not parse worker availability times/i.test(msg) ||
    /invalid time in availability/i.test(msg) ||
    /no tasks supplied/i.test(msg) ||
    /invalid year\/month/i.test(msg) ||
    /calibrat/i.test(msg) ||
    (/min needed/i.test(msg) && /min available/i.test(msg))
  )
}

function isAvailabilityWindowError(msg: string): boolean {
  return (
    /this day'?s visits need/i.test(msg) ||
    /minutes fit between/i.test(msg) ||
    /scheduled visits on this day total/i.test(msg) ||
    /worker availability for that weekday only allows/i.test(msg) ||
    /worker availability only allows \d+ minutes on that weekday/i.test(msg) ||
    /could not parse worker availability times/i.test(msg) ||
    (/min needed/i.test(msg) && /min available/i.test(msg))
  )
}

// ===========================================================================
// Progress steps
// ===========================================================================

function StageRow({
  label,
  state,
  flash,
}: {
  label: string
  state: "pending" | "active" | "done"
  flash: boolean
}) {
  return (
    <li className="flex items-center gap-3">
      <motion.span
        initial={false}
        animate={{
          backgroundColor:
            state === "done"
              ? flash
                ? "var(--success-bg)"
                : "var(--primary-700)"
              : "#FFFFFF",
          borderColor:
            state === "done"
              ? flash
                ? "var(--success)"
                : "var(--primary-700)"
              : state === "active"
                ? "var(--primary-300)"
                : "var(--neutral-300)",
          color:
            state === "done" && flash ? "var(--success)" : "#FFFFFF",
        }}
        transition={{ duration: 0.22, ease: easeOutSoft }}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2"
      >
        {state === "done" && (
          <motion.span
            initial={{ scale: 0, rotate: -90 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ duration: 0.22, ease: easeOutSoft }}
            className="flex"
          >
            <Check className="h-3.5 w-3.5" strokeWidth={3} />
          </motion.span>
        )}
        {state === "active" && (
          <Loader2 className="h-3 w-3 animate-spin text-primary-700" />
        )}
      </motion.span>
      <motion.span
        initial={false}
        animate={{
          color:
            state === "pending"
              ? "var(--neutral-500)"
              : "var(--neutral-900)",
          opacity: state === "pending" ? 0.85 : 1,
        }}
        transition={{ duration: 0.2 }}
        className={cn(
          "text-sm",
          state === "active" ? "font-medium" : "font-normal",
        )}
      >
        {label}
      </motion.span>
    </li>
  )
}

function ProgressSteps({
  stage,
  allDone,
  flash,
}: {
  stage: StageIndex
  allDone: boolean
  flash: boolean
}) {
  return (
    <ul className="flex flex-col gap-3">
      {STAGES.map((label, i) => {
        let state: "pending" | "active" | "done"
        if (allDone || i < stage) state = "done"
        else if (i === stage) state = "active"
        else state = "pending"
        return <StageRow key={label} label={label} state={state} flash={flash} />
      })}
    </ul>
  )
}

// ===========================================================================
// Visual decoration
// ===========================================================================

function IdleIcon({
  drag,
}: {
  drag: DragState
}) {
  if (drag === "invalid") {
    return (
      <motion.div
        key="invalid"
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        transition={{ duration: 0.18, ease: easeOutSoft }}
        className="flex h-12 w-12 items-center justify-center rounded-full bg-danger-bg text-danger"
      >
        <XIcon className="h-6 w-6" strokeWidth={2.5} />
      </motion.div>
    )
  }

  if (drag === "over") {
    return (
      <motion.div
        key="over"
        layout
        animate={{ scale: [1, 1.08, 1] }}
        transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
        className="flex h-16 w-16 items-center justify-center rounded-full bg-primary-100 text-primary-700"
      >
        <FileUp className="h-7 w-7" />
      </motion.div>
    )
  }

  return (
    <motion.div
      key="idle"
      layout
      animate={{ y: [0, -4, 0] }}
      transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
      className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-50 text-primary-700"
    >
      <FileUp className="h-6 w-6" />
    </motion.div>
  )
}

// ===========================================================================
// Main component
// ===========================================================================

export function UploadHero({
  onUploaded,
  variant = "hero",
  authorizedTasksForPreflight,
}: UploadHeroProps) {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const zoneRef = useRef<HTMLDivElement>(null)
  const availabilitySectionRef = useRef<HTMLElement | null>(null)
  const lastFileRef = useRef<File | null>(null)

  const [phase, setPhase] = useState<Phase>({ kind: "idle" })
  const [drag, setDrag] = useState<DragState>("none")
  const [month, setMonth] = useState<MonthYear>(() => currentMonthYear())
  const [shakeKey, setShakeKey] = useState(0)
  const [workerAvailability, setWorkerAvailability] = useState(
    () => defaultWorkerAvailability(),
  )

  const busy = phase.kind === "processing" || phase.kind === "flash"

  // Global `app:focus-upload` — fired by ⌘N and the command palette's "New
  // plan" action. Scrolls the hero into view and triggers the file picker.
  useEffect(() => {
    const handler = () => {
      zoneRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
      if (!busy) {
        window.setTimeout(() => inputRef.current?.click(), 150)
      }
    }
    window.addEventListener("app:focus-upload", handler)
    return () => window.removeEventListener("app:focus-upload", handler)
  }, [busy])

  // -----------------------------------------------------------------------
  // The upload orchestrator — fires the real API in parallel with a paced
  // stage animation, then picks one of four terminal behaviors.
  // -----------------------------------------------------------------------

  // `runUpload` depends on current `month` state so the picker selection at
  // click/drop time is what gets submitted. Reading `month` inside the
  // callback is correct — the linter wants it listed in deps.
  const runUpload = useCallback(
    async (file: File, availabilityOverride?: typeof workerAvailability) => {
      if (!isPdfFile(file)) {
        setShakeKey((n) => n + 1)
        setPhase({
          kind: "parseError",
          message: "That doesn't look like a PDF. Expected MDHHS-6064-P format.",
        })
        return
      }

      lastFileRef.current = file

      // Snapshot the picker at submit time so it can't drift mid-upload.
      const submitMonth = month
      const submitAvailability =
        availabilityOverride ?? workerAvailability

      if (submitAvailability) {
        const visitWeekdays =
          authorizedTasksForPreflight && authorizedTasksForPreflight.length > 0
            ? visitWeekdaysUnionFromAuthorizedTasks(authorizedTasksForPreflight)
            : undefined
        const bad = firstAvailabilityPreflightViolation(submitAvailability, {
          visitWeekdays,
        })
        if (bad) {
          setShakeKey((n) => n + 1)
          toast.error(`Fix worker hours first — ${bad.day} is ${bad.note}`, {
            duration: 10_000,
          })
          window.requestAnimationFrame(() => {
            availabilitySectionRef.current?.scrollIntoView({
              behavior: "smooth",
              block: "center",
            })
          })
          return
        }
      }

      setPhase({ kind: "processing", stage: 0, allDone: false })

      // Pace the visual stages regardless of real API speed — this turns an
      // otherwise opaque "spinner" into a sequence the user can read.
      const paced = (async () => {
        for (let i = 1; i < STAGES.length; i++) {
          await sleep(STAGE_DURATION_MS)
          setPhase((p) =>
            p.kind === "processing"
              ? { ...p, stage: i as StageIndex }
              : p,
          )
        }
        await sleep(STAGE_DURATION_MS)
        setPhase((p) =>
          p.kind === "processing" ? { ...p, allDone: true } : p,
        )
      })()

      // Real work — happens in parallel. We call fetch directly rather than
      // the shared `uploadPDF()` helper so errors surface as inline panels
      // instead of global toasts (less noise during the morph).
      let result: UploadResult | null = null
      let errorMessage: string | null = null
      try {
        result = await uploadPDF(file, submitMonth, submitAvailability)
      } catch (err) {
        if (err instanceof ApiError && isDayCapacityDetail(err.detail)) {
          setShakeKey((n) => n + 1)
          await paced
          setPhase({
            kind: "parseError",
            title: "Worker hours don't fit these visits",
            message: `${err.detail.weekday}: ${err.detail.needed_minutes} min needed / ${err.detail.available_minutes} min available.`,
            dayCapacity: err.detail,
          })
          return
        }
        errorMessage =
          err instanceof Error
            ? err.message
            : "Couldn't read this PDF. Expected MDHHS-6064-P format."
      }

      // Let the stages finish their animation before we show the outcome.
      await paced

      if (errorMessage || !result) {
        setShakeKey((n) => n + 1)
        const raw = (errorMessage ?? "").trim()
        // The backend rejects fully-unreadable PDFs with this phrase after it
        // has already tried pdfplumber → pypdfium2 → RapidOCR.
        const isScanned = /Could not read any text/i.test(raw)
        const isNetwork = isLikelyNetworkError(raw)
        const isSchedule = isScheduleBuildErrorMessage(raw)
        const isAvailWindow = isAvailabilityWindowError(raw)
        const isCalibrationMonth =
          /calibrat/i.test(raw) && !isAvailWindow && !/invalid time in availability/i.test(raw)

        let message: string
        let title: string | undefined
        if (isCalibrationMonth) {
          title = "Can't schedule this month"
          message =
            `Calibration not yet supported for ${formatMonthYear(submitMonth)} ` +
            `(calendar edge case). Try a different month or contact support. (${raw})`
        } else if (isSchedule) {
          title = isAvailWindow
            ? "Worker hours don't fit these visits"
            : "Can't build schedule"
          message = isAvailWindow
            ? `${raw}\n\nThe PDF was read successfully. Use «When can visits be scheduled?» above: the weekday named in the message must allow enough minutes between «From» and the end time used on visit days («To», or «Latest (if visits)» when checked). Reversed «From»/«To» often creates a one-hour window. You can also reduce minutes per task on the authorization if appropriate.`
            : raw || "Couldn't build a schedule from this plan."
        } else if (isScanned) {
          message =
            `We tried native extraction and OCR on this PDF and couldn't recover ` +
            `any text. If it's a scanned copy, try a higher-resolution version; ` +
            `otherwise upload the original searchable PDF from the MDHHS portal.`
        } else if (isNetwork) {
          title = "Can't reach the server"
          message =
            `The browser could not reach the API (often \`npm run dev\` isn't running, or the app is on the wrong URL). ` +
            `From the project root run \`npm run dev\`: FastAPI serves on http://127.0.0.1:8001 and Vite uses http://localhost:3456 ` +
            `by default (\`/api\` is proxied to the backend). If 3456 is busy, open the Local URL Vite prints in the terminal, then reload and try again.`
        } else {
          message = raw || "Couldn't read this PDF. Expected MDHHS-6064-P format."
        }
        setPhase(
          isNetwork
            ? { kind: "parseError", title: "Can't reach the server", message }
            : { kind: "parseError", message, title },
        )
        return
      }

      const rawCid = String(result.client?.client_id ?? "").trim()
      const cid =
        rawCid ||
        (typeof result.client?.id === "number" && Number.isFinite(result.client.id)
          ? `draft_${result.client.id}`
          : "")
      if (!result.plan || !cid) {
        setShakeKey((n) => n + 1)
        toast.error(
          !result.plan
            ? "Upload finished but no plan row was returned. Check the API response or reload."
            : "Upload finished without a usable client identifier. Check whether the PDF includes the MDHHS client ID.",
        )
        onUploaded?.(result)
        setPhase({ kind: "idle" })
        return
      }

      onUploaded?.(result)

      const checks = result.plan.validation?.checks ?? []
      const total = checks.length
      const failed = checks.filter((c) => !c.passed).length
      const name = result.client.client_name?.trim() || cid
      const clientPath = `/clients/${encodeURIComponent(cid)}`

      try {
        if (result.plan.validation_passed) {
          // Quick success flash on all four check circles, then fade the card
          // out and navigate. The AppShell page transition completes the feel.
          setPhase({ kind: "flash" })
          toast.success(`Plan generated for ${name}`, {
            description:
              total > 0 ? `All ${total} checks passed.` : "Schedule and outputs ready.",
            duration: 5000,
          })
          await sleep(260)
          // `fromUpload: true` — ClientDetail uses this to fire the one-time
          // confetti on the reconciliation banner.
          navigate(clientPath, { state: { fromUpload: true } })
        } else {
          setShakeKey((n) => n + 1)
          toast.warning(`Plan generated with warnings for ${name}`, {
            description:
              total > 0
                ? `${failed} of ${total} checks need review.`
                : "Some checks need review.",
            duration: 7000,
          })
          // Hint to ClientDetail to focus the Validation tab.
          navigate(clientPath, { state: { focusTab: "reconciliation" } })
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        toast.error(`Could not open the client page: ${msg}`)
        setPhase({ kind: "idle" })
      }
    },
    [
      authorizedTasksForPreflight,
      month,
      navigate,
      onUploaded,
      workerAvailability,
    ],
  )

  // -----------------------------------------------------------------------
  // Drag & drop
  // -----------------------------------------------------------------------

  const onDragEnter = (e: ReactDragEvent<HTMLDivElement>) => {
    if (busy) return
    e.preventDefault()
    e.stopPropagation()
    setDrag(dragHasPdf(e) ? "over" : "invalid")
  }

  const onDragOver = (e: ReactDragEvent<HTMLDivElement>) => {
    if (busy) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = dragHasPdf(e) ? "copy" : "none"
  }

  const onDragLeave = (e: ReactDragEvent<HTMLDivElement>) => {
    if (busy) return
    e.preventDefault()
    e.stopPropagation()
    // Ignore transitions into child elements.
    if (
      e.currentTarget.contains(e.relatedTarget as Node | null) ||
      e.relatedTarget === null
    ) {
      return
    }
    setDrag("none")
  }

  const onDrop = (e: ReactDragEvent<HTMLDivElement>) => {
    if (busy) return
    e.preventDefault()
    e.stopPropagation()
    const file = e.dataTransfer.files?.[0]
    setDrag("none")
    if (file) void runUpload(file)
  }

  const openPicker = () => {
    if (busy) return
    inputRef.current?.click()
  }

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (busy) return
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      openPicker()
    }
  }

  const onInputChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const f = e.target.files?.[0]
    e.target.value = ""
    if (f) void runUpload(f)
  }

  // Global listeners to swallow stray drops outside the zone (prevents the
  // browser from navigating to the PDF) while the hero is mounted.
  useEffect(() => {
    const stop = (e: DragEvent) => {
      e.preventDefault()
    }
    window.addEventListener("dragover", stop)
    window.addEventListener("drop", stop)
    return () => {
      window.removeEventListener("dragover", stop)
      window.removeEventListener("drop", stop)
    }
  }, [])

  // -----------------------------------------------------------------------
  // Styling derived from state
  // -----------------------------------------------------------------------

  const isHero = variant === "hero"

  const cardClasses = useMemo(() => {
    if (phase.kind === "parseError") {
      return "border-danger bg-danger-bg"
    }
    if (drag === "invalid") {
      return "border-danger bg-danger-bg"
    }
    if (drag === "over") {
      return "border-solid border-primary-500 bg-primary-50 shadow-lg"
    }
    return cn(
      "border-dashed border-neutral-300 bg-gradient-to-b from-neutral-50 to-white shadow-sm",
      !busy &&
        "hover:border-neutral-400 hover:shadow-md",
    )
  }, [drag, phase.kind, busy])

  const headline =
    drag === "invalid"
      ? "PDF files only"
      : drag === "over"
        ? "Release to upload"
        : "Drop an MDHHS-6064 to get started"

  // -----------------------------------------------------------------------
  // Compact variant — slim 100px strip once the user has existing clients.
  // -----------------------------------------------------------------------

  if (!isHero && phase.kind === "idle") {
    return (
      <>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={onInputChange}
          aria-hidden
        />
        <div className="flex flex-col gap-4">
          <WorkerAvailabilitySection
            ref={availabilitySectionRef}
            value={workerAvailability}
            onChange={setWorkerAvailability}
            disabled={busy}
          />
          <motion.div
            role="button"
            tabIndex={0}
            aria-label="Upload MDHHS-6064 PDF"
            ref={zoneRef}
            onClick={openPicker}
            onKeyDown={onKeyDown}
            onDragEnter={onDragEnter}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            animate={shakeKey ? { x: [-4, 4, -2, 2, 0] } : { x: 0 }}
            transition={{ duration: 0.4, ease: easeOutSoft }}
            className={cn(
              "group flex h-[100px] w-full cursor-pointer items-center justify-between rounded-xl border-2 px-6 transition-[border-color,background-color,box-shadow] duration-[160ms] ease-out",
              cardClasses,
            )}
          >
            <div className="flex items-center gap-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-700">
                <FileUp className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold text-neutral-900">
                  Upload another MDHHS-6064
                </div>
                <div className="text-xs text-neutral-600">
                  PDF only • never leaves this computer
                </div>
              </div>
            </div>
            <div
              className="flex items-center gap-2"
              onClick={(e) => e.stopPropagation()}
            >
              <MonthYearPicker value={month} onChange={setMonth} disabled={busy} />
              <Button
                type="button"
                size="default"
                onClick={openPicker}
                className="gap-2"
              >
                <Upload className="h-4 w-4" />
                Choose file
              </Button>
            </div>
          </motion.div>
        </div>
      </>
    )
  }

  // -----------------------------------------------------------------------
  // Hero variant (and compact-while-processing share the same body).
  // -----------------------------------------------------------------------

  return (
    <div className="mb-8">
      {((variant === "hero" &&
        (phase.kind === "idle" || phase.kind === "parseError")) ||
        (variant === "compact" && phase.kind === "parseError")) && (
          <WorkerAvailabilitySection
            ref={availabilitySectionRef}
            value={workerAvailability}
            onChange={setWorkerAvailability}
            disabled={busy}
            className="mb-4"
          />
        )}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        onChange={onInputChange}
        aria-hidden
      />

      <motion.div
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-label="Upload MDHHS-6064 PDF"
        aria-busy={busy}
        ref={zoneRef}
        onClick={phase.kind === "idle" ? openPicker : undefined}
        onKeyDown={onKeyDown}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        animate={{
          scale: drag === "over" ? 1.01 : 1,
          x: shakeKey ? [-4, 4, -2, 2, 0] : 0,
          opacity: phase.kind === "flash" ? [1, 1, 0] : 1,
        }}
        transition={{
          scale: { duration: 0.16, ease: easeOutSoft },
          x: { duration: 0.4, ease: easeOutSoft },
          opacity: { duration: 0.5, times: [0, 0.5, 1], ease: easeOutSoft },
        }}
        className={cn(
          "relative flex min-h-[280px] w-full flex-col items-center justify-center rounded-xl border-2 px-6 py-10 text-center",
          "transition-[border-color,background-color,box-shadow] duration-[160ms] ease-out",
          phase.kind === "idle" ? "cursor-pointer" : "cursor-default",
          cardClasses,
        )}
      >
        <AnimatePresence mode="wait" initial={false}>
          {(phase.kind === "idle" || phase.kind === "parseError") && (
            <motion.div
              key="idle-body"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.2, ease: easeOutSoft }}
              className="flex flex-col items-center gap-4"
            >
              <AnimatePresence mode="wait" initial={false}>
                <IdleIcon key={drag} drag={drag} />
              </AnimatePresence>

              <div className="flex flex-col gap-1">
                <h2 className="font-display text-xl font-semibold tracking-tight text-neutral-900">
                  {headline}
                </h2>
                <p className="text-sm text-neutral-600">
                  PDF only. Your file never leaves this computer.
                </p>
              </div>

              <div
                className="mt-2 flex items-center gap-3"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                <Button
                  type="button"
                  size="lg"
                  onClick={openPicker}
                  className="gap-2"
                >
                  <Upload className="h-4 w-4" />
                  Choose file
                </Button>
                <MonthYearPicker
                  value={month}
                  onChange={setMonth}
                  disabled={busy}
                />
              </div>

              <p className="mt-1 text-[11px] text-neutral-500">
                Typically 60–80 KB
              </p>
            </motion.div>
          )}

          {(phase.kind === "processing" || phase.kind === "flash") && (
            <motion.div
              key="processing-body"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.2, ease: easeOutSoft }}
              className="flex flex-col items-center gap-4"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-50 text-primary-700">
                <FileUp className="h-6 w-6" />
              </div>
              <ProgressSteps
                stage={phase.kind === "processing" ? phase.stage : 3}
                allDone={phase.kind === "flash" || (phase.kind === "processing" && phase.allDone)}
                flash={phase.kind === "flash"}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* Inline error panel (PDF parse failure or schedule calibration failure) */}
      <AnimatePresence initial={false}>
        {phase.kind === "parseError" && (
          <motion.div
            key="error"
            initial={{ opacity: 0, y: -4, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -4, height: 0 }}
            transition={{ duration: 0.2, ease: easeOutSoft }}
            className="mt-3 overflow-hidden"
          >
            <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              <div className="flex-1">
                {phase.message.startsWith("We tried native extraction") ? (
                  <>
                    <p className="font-medium">We couldn't read this PDF</p>
                    <p className="mt-0.5 text-xs text-red-800/80">
                      {phase.message}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="font-medium">
                      {phase.title ?? "Couldn't read this PDF"}
                    </p>
                    {phase.dayCapacity ? (
                      <div className="mt-2">
                        <DayCapacityErrorPanel
                          detail={phase.dayCapacity}
                          availability={workerAvailability}
                          onApplyAvailability={async (next) => {
                            setWorkerAvailability(next)
                            const f = lastFileRef.current
                            if (f) await runUpload(f, next)
                          }}
                          onEditAuthorization={() => {
                            availabilitySectionRef.current?.scrollIntoView({
                              behavior: "smooth",
                              block: "center",
                            })
                          }}
                        />
                      </div>
                    ) : (
                      <p className="mt-0.5 whitespace-pre-line text-xs text-red-800/80">
                        {phase.message}
                      </p>
                    )}
                  </>
                )}
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setPhase({ kind: "idle" })
                  openPicker()
                }}
                className="shrink-0 border-red-300 bg-white text-red-900 hover:bg-red-50"
              >
                Try again
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default UploadHero
