import { toast } from "sonner"
import type { WorkerAvailabilityMap } from "@/lib/workerAvailability"
import type {
  Client,
  ClientDetail,
  ClientSummary,
  DayCapacityDetail,
  HealthResponse,
  PatchPlanConfigRequest,
  PatchPlanRequest,
  Plan,
  PreviewCalibrateBody,
  PreviewCalibrateOut,
  PreviewParseOut,
  RerunPlanRequest,
  ScheduleConfig,
  UploadResult,
  ValidationReport,
} from "@/types"

const JSON_HEADERS = { "Content-Type": "application/json" } as const

/** e.g. `http://127.0.0.1:8000` — paths already include `/api/…`; strip a mistaken trailing `/api` from env. */
function normalizeApiOrigin(raw: string): string {
  let s = raw.trim().replace(/\/+$/, "")
  if (/\/api$/i.test(s)) {
    s = s.replace(/\/api$/i, "").replace(/\/+$/, "")
  }
  return s
}

/** When set, all `/api/*` calls go there directly — useful if the static host has no `/api` proxy. */
const API_ORIGIN = normalizeApiOrigin(import.meta.env.VITE_API_BASE_URL ?? "")

/** Shown when upload/preview calls get routing/proxy failures instead of JSON from FastAPI. */
const API_UPLOAD_ROUTING_HINT =
  "Usually the FastAPI backend is not running on port 8001 (default npm run dev:backend — port 8000 is often occupied). From the project root directory: run `npm run dev:backend` (API only) or `npm run dev` (starts API + Vite). Need a Python venv? Create `.venv` at the repo root and `pip install -r backend/requirements.txt` — see `scripts/dev-backend.cjs`. Override port: `MDHHS_BACKEND_PORT=8000` with matching `VITE_BACKEND_PROXY` in vite. Optional: `VITE_API_BASE_URL=http://127.0.0.1:8001` with no trailing `/api`."

export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/api/${path}`
  if (!API_ORIGIN) return normalized
  return `${API_ORIGIN}${normalized}`
}

function uploadRelatedRequestUrl(url: string): boolean {
  return (
    url.includes("/clients/upload") ||
    url.includes("/clients/preview-parse") ||
    url.includes("/clients/preview-calibrate")
  )
}

function looksLikeUpstreamRoutingFailure(res: Response, message: string): boolean {
  if (!uploadRelatedRequestUrl(typeof res.url === "string" ? res.url : "")) return false
  const t = message.trim()
  if (res.status === 404) return true
  if (t.toLowerCase() === "not found") return true
  if (/^[45]\d\d\s+not\s+found$/i.test(t)) return true
  return false
}

function maybeAppendApiRoutingHint(message: string, res: Response): string {
  if (!looksLikeUpstreamRoutingFailure(res, message)) return message
  if (message.includes("VITE_API_BASE_URL")) return message
  return `${message}\n\n${API_UPLOAD_ROUTING_HINT}`
}

function formatDayCapacityDetailMessage(o: DayCapacityDetail): string {
  const lines: string[] = [
    `${o.weekday}: ${o.needed_minutes} min needed / ${o.available_minutes} min available`,
    `(${o.earliest} – ${o.latest})`,
  ]
  for (const s of o.suggestions ?? []) {
    lines.push(`• ${s.label}`)
  }
  return lines.join("\n")
}

export function isDayCapacityDetail(x: unknown): x is DayCapacityDetail {
  if (!x || typeof x !== "object") return false
  const o = x as Record<string, unknown>
  return (
    o.code === "DAY_CAPACITY_EXCEEDED" &&
    typeof o.weekday === "string" &&
    typeof o.needed_minutes === "number" &&
    typeof o.available_minutes === "number"
  )
}

/** Thrown on non-OK responses; carries FastAPI `detail` when JSON included it. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

function parseErrorMessage(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === "string") return d
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          if (typeof e === "object" && e && "msg" in e) {
            const o = e as { msg?: string; type?: string; loc?: unknown }
            const loc = Array.isArray(o.loc) ? o.loc.join(".") : ""
            const bits = [o.type, loc, o.msg].filter(Boolean)
            return bits.length ? bits.join(" — ") : String(e)
          }
          return String(e)
        })
        .join("; ")
    }
    if (typeof d === "object" && d !== null && !Array.isArray(d)) {
      const o = d as Record<string, unknown>
      if (o.code === "AUTHORIZATION_EXCEEDS_WEEKLY_CAPACITY") {
        const msg =
          typeof o.message === "string" && o.message.trim()
            ? o.message.trim()
            : "Authorized minutes exceed total weekly worker capacity."
        return msg
      }
      if (isDayCapacityDetail(d)) {
        return formatDayCapacityDetailMessage(d)
      }
      if (typeof o.message === "string" && o.message.trim()) {
        return o.message
      }
      try {
        return JSON.stringify(d)
      } catch {
        return "Request failed"
      }
    }
  }
  return "Request failed"
}

/** Read error message/detail without showing a toast (for preflight endpoints). */
async function readResponseError(res: Response): Promise<{ message: string; detail: unknown }> {
  let detail: unknown
  let message = `${res.status} ${res.statusText || ""}`.trim()
  try {
    const j: unknown = await res.json()
    message = parseErrorMessage(j) || message
    if (j && typeof j === "object" && "detail" in j) {
      detail = (j as { detail: unknown }).detail
    }
  } catch {
    detail = undefined
  }
  return { message: maybeAppendApiRoutingHint(message, res), detail }
}

async function throwIfResNotOk(res: Response): Promise<void> {
  if (res.ok) return
  let detail: unknown
  let detailStr = `${res.status} ${res.statusText || ""}`.trim()
  try {
    const j: unknown = await res.json()
    if (j && typeof j === "object" && "detail" in j) {
      detail = (j as { detail: unknown }).detail
    }
    detailStr = parseErrorMessage(j) || detailStr
  } catch {
    detail = undefined
  }
  detailStr = maybeAppendApiRoutingHint(detailStr, res)

  // 422 — validation from the backend. Keep the specific message; it's the
  // most useful kind of error.
  if (res.status === 422) {
    toast.error(detailStr, { duration: 8000 })
  } else if (res.status >= 500) {
    // 500-class — user can't fix it from the UI. Point at the terminal so
    // the operator knows where to look.
    toast.error("Something went wrong on the server", {
      description: "Check the terminal logs for details.",
      duration: 8000,
    })
  } else {
    toast.error(detailStr, { duration: 8000 })
  }

  throw new ApiError(detailStr, res.status, detail)
}

export type DownloadFileType = "xlsx" | "pdf" | "source" | "weekly"

/**
 * POST /api/clients/upload
 *
 * `serviceMonth` is the calendar month the generated schedule will be
 * calibrated against. Omit to let the backend fall back to the current
 * year/month.
 */
export type { WorkerAvailabilityMap } from "@/lib/workerAvailability"

/**
 * POST /api/clients/preview-parse — extract tasks from PDF only (no DB, no schedule).
 * Does not toast; callers should show inline errors for 4xx.
 */
export async function previewParsePdf(
  file: File,
  signal?: AbortSignal
): Promise<PreviewParseOut> {
  const form = new FormData()
  form.set("file", file)
  const res = await fetch(apiUrl("/api/clients/preview-parse"), {
    method: "POST",
    body: form,
    signal,
  })
  if (!res.ok) {
    const { message, detail } = await readResponseError(res)
    throw new ApiError(message, res.status, detail)
  }
  return (await res.json()) as PreviewParseOut
}

/**
 * POST /api/clients/preview-calibrate — dry-run capacity + schedule (no persistence).
 * Does not toast; callers handle `schedule_ok`, `day_capacity`, and other fields.
 */
export async function previewCalibrate(
  body: PreviewCalibrateBody,
  signal?: AbortSignal
): Promise<PreviewCalibrateOut> {
  const res = await fetch(apiUrl("/api/clients/preview-calibrate"), {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    const { message, detail } = await readResponseError(res)
    throw new ApiError(message, res.status, detail)
  }
  return (await res.json()) as PreviewCalibrateOut
}

export async function uploadPDF(
  file: File,
  serviceMonth?: { year: number; month: number },
  workerAvailability?: WorkerAvailabilityMap
): Promise<UploadResult> {
  const form = new FormData()
  form.set("file", file)
  if (serviceMonth) {
    form.set("year", String(serviceMonth.year))
    form.set("month", String(serviceMonth.month))
  }
  if (workerAvailability) {
    form.set("availability_json", JSON.stringify(workerAvailability))
  }
  const res = await fetch(apiUrl("/api/clients/upload"), {
    method: "POST",
    body: form,
  })
  await throwIfResNotOk(res)
  return (await res.json()) as UploadResult
}

/** Defensive: same `client_id` should not appear twice; keeps UI stable if callers double-fetch. */
function dedupeClientsById(rows: ClientSummary[]): ClientSummary[] {
  const seen = new Set<string>()
  const out: ClientSummary[] = []
  for (const r of rows) {
    const id = String(r.client_id ?? "").trim()
    if (!id || seen.has(id)) continue
    seen.add(id)
    out.push(r)
  }
  return out
}

/**
 * GET /api/clients
 */
export async function listClients(): Promise<ClientSummary[]> {
  const res = await fetch(apiUrl("/api/clients"))
  await throwIfResNotOk(res)
  const rows = (await res.json()) as ClientSummary[]
  return dedupeClientsById(rows)
}

/**
 * GET /api/clients/{clientId}
 */
export async function getClient(clientId: string): Promise<ClientDetail> {
  const res = await fetch(apiUrl(`/api/clients/${encodeURIComponent(clientId)}`))
  await throwIfResNotOk(res)
  return (await res.json()) as ClientDetail
}

/**
 * PATCH /api/clients/{clientId} — update worker availability windows.
 * With `regenerate: true`, also re-calibrates the latest plan and returns that Plan.
 */
export async function patchClientAvailability(
  clientId: string,
  availability: WorkerAvailabilityMap,
  options?: {
    regenerate?: boolean
    shared_living?: boolean
    iadl_separate_documented?: boolean
  },
): Promise<Client | Plan> {
  const q =
    options?.regenerate === true
      ? "?regenerate=true"
      : ""
  const body: Record<string, unknown> = {
    availability,
    ...(typeof options?.shared_living === "boolean"
      ? { shared_living: options.shared_living }
      : {}),
    ...(typeof options?.iadl_separate_documented === "boolean"
      ? { iadl_separate_documented: options.iadl_separate_documented }
      : {}),
  }
  const res = await fetch(
    apiUrl(`/api/clients/${encodeURIComponent(clientId)}${q}`),
    {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Client | Plan
}

/**
 * POST …/plans/{v}/preview — calibrate with draft availability; no DB or artifact writes.
 */
export async function previewPlanWithAvailability(
  clientId: string,
  planVersion: number,
  availability: WorkerAvailabilityMap,
  signal?: AbortSignal,
): Promise<Plan> {
  const res = await fetch(
    apiUrl(
      `/api/clients/${encodeURIComponent(clientId)}/plans/${planVersion}/preview`,
    ),
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ availability }),
      signal,
    },
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Plan
}

/**
 * GET /api/clients/{clientId}/plans/{version}
 */
export async function getPlan(clientId: string, version: number): Promise<Plan> {
  const res = await fetch(
    apiUrl(`/api/clients/${encodeURIComponent(clientId)}/plans/${version}`)
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Plan
}

/**
 * POST /api/clients/{clientId}/plans/{version}/validate — dry-run cross_check (no save).
 */
export async function validatePlan(
  clientId: string,
  version: number,
  schedule: Record<string, unknown>
): Promise<ValidationReport> {
  const res = await fetch(
    apiUrl(`/api/clients/${encodeURIComponent(clientId)}/plans/${version}/validate`),
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ schedule }),
    }
  )
  await throwIfResNotOk(res)
  return (await res.json()) as ValidationReport
}

/**
 * PATCH /api/clients/{clientId}/plans/{version}
 */
export async function updatePlan(
  clientId: string,
  version: number,
  body: PatchPlanRequest
): Promise<Plan> {
  const res = await fetch(
    apiUrl(`/api/clients/${encodeURIComponent(clientId)}/plans/${version}`),
    {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Plan
}

/**
 * POST /api/clients/{clientId}/plans/{version}/rerun
 */
export async function rerunPlan(
  clientId: string,
  version: number,
  body: RerunPlanRequest
): Promise<Plan> {
  const res = await fetch(
    apiUrl(`/api/clients/${encodeURIComponent(clientId)}/plans/${version}/rerun`),
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Plan
}

/**
 * PATCH /api/clients/{clientId}/plans/{version}/config
 *
 * Push a new ScheduleConfig (per-task weekdays + per-weekday start times)
 * to the backend. The server regenerates the daily schedule, re-runs
 * cross_check, re-emits the xlsx/pdf artifacts, and returns the updated
 * Plan row. The editor calls this on each debounced edit so the
 * reconciliation banner stays live.
 */
export async function patchPlanConfig(
  clientId: string,
  version: number,
  config: ScheduleConfig,
  options?: { reseedPlacement?: boolean },
): Promise<Plan> {
  const body: PatchPlanConfigRequest = {
    config,
    ...(options?.reseedPlacement === true
      ? { reseed_placement: true }
      : {}),
  }
  const res = await fetch(
    apiUrl(
      `/api/clients/${encodeURIComponent(clientId)}/plans/${version}/config`
    ),
    {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }
  )
  await throwIfResNotOk(res)
  return (await res.json()) as Plan
}

/**
 * Returns a same-origin URL for opening or downloading. Use with <a href> or window.open
 * in dev, Vite proxies /api to the backend.
 */
export function downloadFile(
  clientId: string,
  version: number,
  filetype: DownloadFileType
): string {
  return apiUrl(
    `/api/clients/${encodeURIComponent(clientId)}/plans/${version}/download/${filetype}`
  )
}

/**
 * DELETE /api/clients/{clientId}
 */
export async function deleteClient(clientId: string): Promise<void> {
  const res = await fetch(apiUrl(`/api/clients/${encodeURIComponent(clientId)}`), {
    method: "DELETE",
  })
  await throwIfResNotOk(res)
}

/**
 * GET /api/health
 */
export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(apiUrl("/api/health"))
  await throwIfResNotOk(res)
  return (await res.json()) as HealthResponse
}

export type { Client, ClientDetail, Plan, UploadResult }
