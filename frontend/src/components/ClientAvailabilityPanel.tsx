import { useCallback, useEffect, useRef, useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import {
  patchClientAvailability,
  previewPlanWithAvailability,
} from "@/api/client"
import { WorkerAvailabilitySection } from "@/components/upload/WorkerAvailabilitySection"
import { Button } from "@/components/ui/button"
import {
  defaultWorkerAvailability,
  normalizeWorkerAvailability,
  type WorkerAvailabilityMap,
} from "@/lib/workerAvailability"
import { cn } from "@/lib/utils"
import type { Client, Plan } from "@/types"

const PREVIEW_DEBOUNCE_MS = 480

type Props = {
  client: Client
  planVersion: number | null
  latestPlanVersion: number | null
  /** `null` when draft matches server — parent should use `client.availability`. */
  onAvailabilityDraftChange: (draft: WorkerAvailabilityMap | null) => void
  onPlanPreview: (plan: Plan) => void
  onPlanRegenerated: (plan: Plan, savedAvailability: WorkerAvailabilityMap) => void
}

export function ClientAvailabilityPanel({
  client,
  planVersion,
  latestPlanVersion,
  onAvailabilityDraftChange,
  onPlanPreview,
  onPlanRegenerated,
}: Props) {
  const [value, setValue] = useState<WorkerAvailabilityMap>(() =>
    client.availability
      ? normalizeWorkerAvailability(client.availability)
      : defaultWorkerAvailability(),
  )
  const [sharedLiving, setSharedLiving] = useState(() => client.shared_living ?? false)
  const [iadlSeparateDoc, setIadlSeparateDoc] = useState(
    () => client.iadl_separate_documented ?? false,
  )
  const [previewOnly, setPreviewOnly] = useState(true)
  const [saving, setSaving] = useState(false)

  /** Browser timer id — use `number` to avoid Node `Timeout` vs DOM mismatch under `tsc`. */
  const previewTimerRef = useRef<number | null>(null)
  const previewInFlightRef = useRef(false)
  const previewPendingRef = useRef<WorkerAvailabilityMap | null>(null)
  const previewAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const next = client.availability
      ? normalizeWorkerAvailability(client.availability)
      : defaultWorkerAvailability()
    setValue(next)
    setSharedLiving(client.shared_living ?? false)
    setIadlSeparateDoc(client.iadl_separate_documented ?? false)
    onAvailabilityDraftChange(null)
    // Intentionally omit onAvailabilityDraftChange — parent may pass an unstable ref.
  }, [
    client.client_id,
    client.updated_at,
    client.availability,
    client.shared_living,
    client.iadl_separate_documented,
  ])

  const clearPreviewTimer = () => {
    if (previewTimerRef.current) {
      window.clearTimeout(previewTimerRef.current)
      previewTimerRef.current = null
    }
  }

  const abortPreviewInFlight = () => {
    previewAbortRef.current?.abort()
    previewAbortRef.current = null
  }

  const runPreviewRequest = useCallback(
    async (draft: WorkerAvailabilityMap) => {
      if (planVersion == null) return
      if (previewInFlightRef.current) {
        previewPendingRef.current = draft
        return
      }
      const ctrl = new AbortController()
      previewAbortRef.current = ctrl
      previewInFlightRef.current = true
      try {
        const plan = await previewPlanWithAvailability(
          client.client_id,
          planVersion,
          draft,
          ctrl.signal,
        )
        onPlanPreview(plan)
      } catch (e: unknown) {
        if (e instanceof Error && e.name === "AbortError") return
        /* throwIfResNotOk toasts */
      } finally {
        previewInFlightRef.current = false
        previewAbortRef.current = null
        const pending = previewPendingRef.current
        previewPendingRef.current = null
        if (pending) void runPreviewRequest(pending)
      }
    },
    [client.client_id, planVersion, onPlanPreview],
  )

  const schedulePreview = useCallback(
    (draft: WorkerAvailabilityMap) => {
      if (!previewOnly || planVersion == null) return
      clearPreviewTimer()
      previewTimerRef.current = window.setTimeout(() => {
        previewTimerRef.current = null
        void runPreviewRequest(draft)
      }, PREVIEW_DEBOUNCE_MS)
    },
    [previewOnly, planVersion, runPreviewRequest],
  )

  const prevPreviewOnlyRef = useRef(previewOnly)
  useEffect(() => {
    const turnedOn = !prevPreviewOnlyRef.current && previewOnly
    const turnedOff = prevPreviewOnlyRef.current && !previewOnly
    prevPreviewOnlyRef.current = previewOnly

    if (turnedOff) {
      clearPreviewTimer()
      abortPreviewInFlight()
      previewPendingRef.current = null
    } else if (turnedOn && planVersion != null) {
      schedulePreview(value)
    }
  }, [previewOnly, planVersion, value, schedulePreview])

  useEffect(() => {
    return () => {
      clearPreviewTimer()
      abortPreviewInFlight()
    }
  }, [])

  const handleValueChange = (next: WorkerAvailabilityMap) => {
    setValue(next)
    onAvailabilityDraftChange(next)
    schedulePreview(next)
  }

  const save = async () => {
    clearPreviewTimer()
    abortPreviewInFlight()
    previewPendingRef.current = null
    setSaving(true)
    const snapshot = value
    try {
      const plan = (await patchClientAvailability(client.client_id, snapshot, {
        regenerate: true,
        shared_living: sharedLiving,
        iadl_separate_documented: iadlSeparateDoc,
      })) as Plan
      onPlanRegenerated(plan, snapshot)
      toast.success("Availability saved", {
        description:
          latestPlanVersion != null && plan.version !== planVersion
            ? `Latest plan (v${plan.version}) was recalibrated; Excel and PDF are updated.`
            : "Schedule recalibrated — Excel, PDF, and reconciliation updated.",
      })
    } catch {
      /* throwIfResNotOk toasts */
    } finally {
      setSaving(false)
    }
  }

  const viewingLatest =
    planVersion != null &&
    latestPlanVersion != null &&
    planVersion === latestPlanVersion

  return (
    <div className="mb-6 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-900">
            Worker availability
          </h2>
          <p className="mt-0.5 text-xs text-neutral-600">
            Saved on this client. With <span className="font-medium">Preview only</span>, the
            daily schedule and reconciliation update in the tabs after a short debounce
            without writing files. <span className="font-medium">Save availability</span>{" "}
            persists windows and regenerates Excel/PDF for the{" "}
            <span className="font-medium">latest</span> plan version.
          </p>
          {planVersion != null &&
          latestPlanVersion != null &&
          !viewingLatest ? (
            <p className="mt-2 text-xs text-amber-900">
              You are viewing v{planVersion}. Saving regenerates the latest plan (v
              {latestPlanVersion}) and its downloads.
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-stretch gap-2 sm:items-end">
          <label
            className={cn(
              "flex cursor-pointer items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-1.5 text-[11px] text-neutral-800",
              saving && "pointer-events-none opacity-60",
            )}
          >
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-neutral-400"
              checked={previewOnly}
              disabled={saving || planVersion == null}
              onChange={(e) => setPreviewOnly(e.target.checked)}
            />
            <span>
              Preview only
              <span className="mt-0.5 block font-normal text-neutral-500">
                Debounced live schedule (no Excel/PDF until Save).
              </span>
            </span>
          </label>
          <Button
            type="button"
            size="sm"
            onClick={() => void save()}
            disabled={saving || planVersion == null}
            className="gap-2"
          >
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Saving…
              </>
            ) : (
              "Save availability"
            )}
          </Button>
        </div>
      </div>

      <div className="mt-4 space-y-2 border-t border-neutral-100 pt-4">
        <h3 className="text-xs font-medium text-neutral-800">Shared living (ASM 120)</h3>
        <p className="text-[11px] text-neutral-600">
          Saved on the client for validation notes only — does not change authorized minutes.
        </p>
        <label
          className={cn(
            "flex cursor-pointer items-start gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-2 text-[11px] text-neutral-800",
            saving && "pointer-events-none opacity-60",
          )}
        >
          <input
            type="checkbox"
            className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-neutral-400"
            checked={sharedLiving}
            disabled={saving}
            onChange={(e) => setSharedLiving(e.target.checked)}
          />
          <span>Other adults reside in the home</span>
        </label>
        <label
          className={cn(
            "flex cursor-pointer items-start gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-2 text-[11px] text-neutral-800",
            saving && "pointer-events-none opacity-60",
          )}
        >
          <input
            type="checkbox"
            className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded border-neutral-400"
            checked={iadlSeparateDoc}
            disabled={saving}
            onChange={(e) => setIadlSeparateDoc(e.target.checked)}
          />
          <span>IADLs documented as completed separately for this client</span>
        </label>
      </div>

      <WorkerAvailabilitySection
        value={value}
        onChange={handleValueChange}
        disabled={saving}
        hideIntro
        className="mt-4 border-0 bg-transparent p-0 shadow-none"
      />
    </div>
  )
}
