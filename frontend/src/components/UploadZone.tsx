import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { FileText, Loader2, Upload, X } from "lucide-react"
import { toast } from "sonner"
import {
  ApiError,
  isDayCapacityDetail,
  previewCalibrate,
  previewParsePdf,
  uploadPDF,
} from "@/api/client"
import { DayCapacityErrorPanel } from "@/components/DayCapacityErrorPanel"
import { WorkerAvailabilitySection } from "@/components/upload/WorkerAvailabilitySection"
import { Button } from "@/components/ui/button"
import {
  firstAvailabilityPreflightViolation,
  visitWeekdaysUnionFromAuthorizedTasks,
} from "@/lib/scheduleUtils"
import { defaultWorkerAvailability } from "@/lib/workerAvailability"
import { cn } from "@/lib/utils"
import type {
  DayCapacityDetail,
  PreviewCalibrateOut,
  PreviewParseOut,
} from "@/types"

const STATUS_CYCLE = [
  "Extracting tasks from PDF...",
  "Calculating schedule...",
  "Running cross-checks...",
  "Building output files...",
] as const

const ACCEPT = "application/pdf"
const CAL_DEBOUNCE_MS = 380

function isLikelyNetworkError(message: string): boolean {
  const m = message.trim().toLowerCase()
  return (
    m.includes("failed to fetch") ||
    m.includes("networkerror") ||
    m.includes("network request failed") ||
    m.includes("load failed")
  )
}

function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  )
}

function headroomLabel(pct: number): string {
  if (pct >= 0) return `${pct}% headroom`
  return `${Math.abs(pct)}% short`
}

function CapacityStackBar({
  need,
  base,
  extension,
  totalCapacity,
}: {
  need: number
  base: number
  extension: number
  totalCapacity: number
}) {
  const overflow = Math.max(0, need - totalCapacity)
  const scale = Math.max(need, totalCapacity, 1)
  const pct = (v: number) => `${(v / scale) * 100}%`

  return (
    <div className="w-full space-y-1.5">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {base > 0 ? (
          <div
            className="h-full shrink-0 bg-emerald-500 transition-[width] duration-300"
            style={{ width: pct(base) }}
            title={`Base window: ${base} min/wk`}
          />
        ) : null}
        {extension > 0 ? (
          <div
            className="h-full shrink-0 bg-amber-500 transition-[width] duration-300"
            style={{ width: pct(extension) }}
            title={`Visit-day extension: ${extension} min/wk`}
          />
        ) : null}
        {overflow > 0 ? (
          <div
            className="h-full shrink-0 bg-red-500 transition-[width] duration-300"
            style={{ width: pct(overflow) }}
            title={`Over capacity: ${overflow} min/wk`}
          />
        ) : null}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>
          <span className="inline-block h-2 w-2 rounded-sm bg-emerald-500 align-middle" /> Base
        </span>
        <span>
          <span className="inline-block h-2 w-2 rounded-sm bg-amber-500 align-middle" /> Visit-day
          extension
        </span>
        {overflow > 0 ? (
          <span>
            <span className="inline-block h-2 w-2 rounded-sm bg-red-500 align-middle" /> Overflow
          </span>
        ) : null}
      </div>
    </div>
  )
}

type UploadZoneProps = {
  onUploaded: () => void
}

export function UploadZone({ onUploaded }: UploadZoneProps) {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const lastFileRef = useRef<File | null>(null)
  const parseAbortRef = useRef<AbortController | null>(null)
  const calAbortRef = useRef<AbortController | null>(null)
  const calDebounceRef = useRef<number | null>(null)

  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [statusIndex, setStatusIndex] = useState(0)
  const [workerAvailability, setWorkerAvailability] = useState(defaultWorkerAvailability)
  const [uploadDayCapacity, setUploadDayCapacity] = useState<DayCapacityDetail | null>(null)

  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [parseLoading, setParseLoading] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [parsed, setParsed] = useState<PreviewParseOut | null>(null)
  const [calLoading, setCalLoading] = useState(false)
  const [calResult, setCalResult] = useState<PreviewCalibrateOut | null>(null)
  const [calHttpError, setCalHttpError] = useState<string | null>(null)

  useEffect(() => {
    if (!uploading) return
    setStatusIndex(0)
    const id = window.setInterval(() => {
      setStatusIndex((i) => (i + 1) % STATUS_CYCLE.length)
    }, 1500)
    return () => window.clearInterval(id)
  }, [uploading])

  const visitWeekdays = parsed?.tasks?.length
    ? visitWeekdaysUnionFromAuthorizedTasks(parsed.tasks)
    : undefined

  const runCalibrate = useCallback(
    async (preview: PreviewParseOut, availability: typeof workerAvailability) => {
      calAbortRef.current?.abort()
      const ac = new AbortController()
      calAbortRef.current = ac
      setCalLoading(true)
      setCalHttpError(null)
      try {
        const out = await previewCalibrate(
          {
            tasks: preview.tasks,
            availability,
            pay_rate: preview.pay_rate,
          },
          ac.signal,
        )
        if (ac.signal.aborted) return
        setCalResult(out)
        if (out.schedule_ok && !out.day_capacity) {
          setUploadDayCapacity(null)
        }
      } catch (err) {
        if (isAbortError(err)) return
        const message = err instanceof Error ? err.message : String(err)
        if (!ac.signal.aborted) {
          setCalResult(null)
          setCalHttpError(message)
          if (isLikelyNetworkError(message)) {
            toast.error("Could not reach the server", {
              description: "Check your connection or API URL, then try again.",
              duration: 8000,
            })
          }
        }
      } finally {
        if (!ac.signal.aborted) setCalLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    if (!parsed) {
      setCalResult(null)
      setCalHttpError(null)
      return
    }
    if (calDebounceRef.current !== null) {
      window.clearTimeout(calDebounceRef.current)
    }
    calDebounceRef.current = window.setTimeout(() => {
      calDebounceRef.current = null
      void runCalibrate(parsed, workerAvailability)
    }, CAL_DEBOUNCE_MS)
    return () => {
      if (calDebounceRef.current !== null) {
        window.clearTimeout(calDebounceRef.current)
        calDebounceRef.current = null
      }
    }
  }, [parsed, workerAvailability, runCalibrate])

  const runParse = useCallback(async (file: File) => {
    parseAbortRef.current?.abort()
    const ac = new AbortController()
    parseAbortRef.current = ac
    setParseLoading(true)
    setParseError(null)
    setParsed(null)
    setCalResult(null)
    setCalHttpError(null)
    setUploadDayCapacity(null)
    try {
      const out = await previewParsePdf(file, ac.signal)
      if (ac.signal.aborted) return
      setParsed(out)
    } catch (err) {
      if (isAbortError(err)) return
      const message = err instanceof Error ? err.message : String(err)
      if (!ac.signal.aborted) {
        setParseError(message)
        if (isLikelyNetworkError(message)) {
          toast.error("Could not reach the server", {
            description: "Check your connection or API URL, then try again.",
            duration: 8000,
          })
        }
      }
    } finally {
      if (!ac.signal.aborted) setParseLoading(false)
    }
  }, [])

  const clearSelection = useCallback(() => {
    parseAbortRef.current?.abort()
    calAbortRef.current?.abort()
    setSelectedFile(null)
    lastFileRef.current = null
    setParsed(null)
    setParseError(null)
    setCalResult(null)
    setCalHttpError(null)
    setUploadDayCapacity(null)
    setParseLoading(false)
    setCalLoading(false)
  }, [])

  const onFileChosen = useCallback(
    (file: File | undefined) => {
      if (!file) return
      if (file.type !== ACCEPT && !file.name.toLowerCase().endsWith(".pdf")) {
        toast.error("Please choose a PDF file.")
        return
      }
      setSelectedFile(file)
      lastFileRef.current = file
      void runParse(file)
    },
    [runParse],
  )

  const runUpload = useCallback(
    async (file: File, availabilityOverride = workerAvailability) => {
      if (file.type !== ACCEPT && !file.name.toLowerCase().endsWith(".pdf")) {
        toast.error("Please choose a PDF file.")
        return
      }
      const availPreflight = firstAvailabilityPreflightViolation(availabilityOverride, {
        visitWeekdays,
      })
      if (availPreflight) {
        toast.error(
          `Fix worker hours first — ${availPreflight.day} is ${availPreflight.note}`,
          { duration: 10_000 },
        )
        return
      }
      lastFileRef.current = file
      setUploadDayCapacity(null)
      setUploading(true)
      try {
        const result = await uploadPDF(file, undefined, availabilityOverride)
        const name = result.client.client_name?.trim() || result.client.client_id
        toast.success(`Plan of care generated for ${name}`)
        if (!result.plan.validation_passed) {
          toast.warning("Some validation checks did not pass. Review the plan on the detail page.", {
            description: "The schedule and outputs were still generated from the PDF.",
            duration: 8000,
          })
        }
        onUploaded()
        navigate(`/clients/${encodeURIComponent(result.client.client_id)}`)
      } catch (err) {
        if (err instanceof ApiError && isDayCapacityDetail(err.detail)) {
          setWorkerAvailability(availabilityOverride)
          setUploadDayCapacity(err.detail)
        }
      } finally {
        setUploading(false)
      }
    },
    [navigate, onUploaded, workerAvailability, visitWeekdays],
  )

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ""
    onFileChosen(f)
  }

  const preflightDayCapacity =
    calResult?.day_capacity && isDayCapacityDetail(calResult.day_capacity)
      ? calResult.day_capacity
      : null
  const panelDetail = uploadDayCapacity ?? preflightDayCapacity
  const panelMode = uploadDayCapacity ? "upload" : "preflight"

  const authCapErr = calResult?.authorization_capacity_error
  const authCapText =
    authCapErr && typeof authCapErr === "object" && "message" in authCapErr
      ? String((authCapErr as { message?: unknown }).message ?? "")
      : null

  const canUpload =
    !!selectedFile &&
    !!parsed &&
    !parseLoading &&
    !parseError &&
    !uploading

  return (
    <div className="mb-8">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        disabled={uploading}
        onChange={onInputChange}
        aria-hidden
      />
      <button
        type="button"
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setDragActive(true)
        }}
        onDragOver={(e) => {
          e.preventDefault()
          e.stopPropagation()
        }}
        onDragLeave={(e) => {
          e.preventDefault()
          e.stopPropagation()
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setDragActive(false)
          }
        }}
        onDrop={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setDragActive(false)
          if (uploading) return
          const f = e.dataTransfer.files?.[0]
          onFileChosen(f)
        }}
        className={cn(
          "group flex w-full flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors",
          dragActive ? "border-primary bg-primary/5" : "border-muted-foreground/40 bg-muted/20",
          uploading ? "pointer-events-none opacity-90" : "hover:border-primary/60 hover:bg-muted/30",
        )}
      >
        {uploading ? (
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-10 w-10 animate-spin text-primary" aria-hidden />
            <p className="text-sm font-medium text-foreground">{STATUS_CYCLE[statusIndex]}</p>
            <p className="text-xs text-muted-foreground">This usually takes a few seconds.</p>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-center gap-2 text-muted-foreground group-hover:text-foreground">
              <FileText className="h-8 w-8" strokeWidth={1.5} />
              <Upload className="h-8 w-8" strokeWidth={1.5} />
            </div>
            <p className="text-sm text-foreground">
              Drop MDHHS-6064 PDF here, or <span className="font-medium text-primary underline">click to browse</span>
            </p>
            <p className="text-xs text-muted-foreground">
              PDF only — capacity preview runs before you upload
            </p>
          </>
        )}
      </button>

      {selectedFile && !uploading ? (
        <div className="mt-4 space-y-4 rounded-lg border border-border bg-card p-4 text-left shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{selectedFile.name}</p>
              {parsed?.client_name ? (
                <p className="text-xs text-muted-foreground">{parsed.client_name}</p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => inputRef.current?.click()}
                disabled={parseLoading}
              >
                Change file
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => clearSelection()}
                disabled={parseLoading}
                aria-label="Clear file"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {parseLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Reading tasks from PDF…
            </div>
          ) : null}

          {parseError ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {parseError}
            </p>
          ) : null}

          {parsed && !parseError ? (
            <>
              <WorkerAvailabilitySection
                value={workerAvailability}
                onChange={setWorkerAvailability}
                disabled={parseLoading}
                hideIntro
                className="border-neutral-200"
              />

              {calHttpError ? (
                <p className="text-sm text-destructive">{calHttpError}</p>
              ) : null}

              {calResult?.availability_error ? (
                <p className="rounded-md border border-amber-200 bg-amber-50/90 px-3 py-2 text-sm text-amber-950">
                  {calResult.availability_error}
                </p>
              ) : null}

              {authCapText ? (
                <p className="rounded-md border border-amber-200 bg-amber-50/90 px-3 py-2 text-sm text-amber-950">
                  {authCapText}
                </p>
              ) : null}

              {calResult ? (
                <div className="space-y-3 rounded-md border border-border bg-muted/30 px-3 py-3">
                  <p className="text-sm text-foreground">
                    This plan needs{" "}
                    <span className="font-semibold tabular-nums">
                      ~{calResult.weekly_authorized_minutes} min/week
                    </span>
                    . Your availability supports{" "}
                    <span className="font-semibold tabular-nums">
                      ~{calResult.weekly_total_capacity_minutes} min/week
                    </span>{" "}
                    <span className="text-muted-foreground">
                      ({headroomLabel(calResult.headroom_percent)})
                    </span>
                    .
                  </p>
                  <CapacityStackBar
                    need={calResult.weekly_authorized_minutes}
                    base={calResult.weekly_base_capacity_minutes}
                    extension={calResult.weekly_extension_minutes}
                    totalCapacity={calResult.weekly_total_capacity_minutes}
                  />
                  {calLoading ? (
                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Updating preview…
                    </p>
                  ) : null}
                </div>
              ) : calLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Estimating capacity…
                </div>
              ) : null}

              {panelDetail ? (
                <DayCapacityErrorPanel
                  detail={panelDetail}
                  availability={workerAvailability}
                  disabled={uploading || parseLoading || (panelMode === "preflight" && calLoading)}
                  onApplyAvailability={async (next) => {
                    setWorkerAvailability(next)
                    if (panelMode === "upload") {
                      const f = lastFileRef.current
                      if (f) await runUpload(f, next)
                    }
                  }}
                  onEditAuthorization={() => {
                    window.dispatchEvent(new CustomEvent("app:focus-upload"))
                  }}
                />
              ) : null}

              <Button
                type="button"
                className="w-full sm:w-auto"
                disabled={!canUpload}
                onClick={() => {
                  const f = lastFileRef.current
                  if (f) void runUpload(f)
                }}
              >
                <Upload className="mr-2 h-4 w-4" />
                Upload &amp; generate plan
              </Button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
