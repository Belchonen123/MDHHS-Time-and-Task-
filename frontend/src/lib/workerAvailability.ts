import { WEEK_DAYS } from "@/lib/scheduleUtils"

export type WorkerAvailabilityDay = {
  earliest: string
  latest: string
  visit_day_longer?: boolean
  visit_day_latest?: string
  preferred_duration_min?: number
}

export type WorkerAvailabilityMap = Record<string, WorkerAvailabilityDay>

/** Display strings like "1:00 PM" → "13:00" for <input type="time">; "" on parse failure. */
export function toTimeInputValue(display: string): string {
  const m = String(display || "").match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i)
  if (!m) return ""
  let h = Number(m[1])
  const min = m[2]
  const ampm = (m[3] || "").toUpperCase()
  if (ampm === "PM" && h < 12) h += 12
  if (ampm === "AM" && h === 12) h = 0
  return `${String(h).padStart(2, "0")}:${min}`
}

/** "13:30" → "1:30 PM"; falls back to "1:00 PM" on bad input. */
export function fromTimeInputValue(v: string): string {
  const m = String(v || "").match(/^(\d{1,2}):(\d{2})$/)
  if (!m) return "1:00 PM"
  let h = Number(m[1])
  const min = m[2]
  const ampm = h >= 12 ? "PM" : "AM"
  if (h === 0) h = 12
  else if (h > 12) h -= 12
  return `${h}:${min} ${ampm}`
}

export function defaultWorkerAvailability(): WorkerAvailabilityMap {
  const out: WorkerAvailabilityMap = {}
  for (const d of WEEK_DAYS) {
    out[d] = {
      earliest: "1:00 PM",
      latest: "8:00 PM",
      visit_day_longer: false,
      visit_day_latest: "",
    }
  }
  return out
}

/** Merge API payload (may be partial) onto defaults. */
export function normalizeWorkerAvailability(
  raw: Record<string, unknown> | null | undefined,
): WorkerAvailabilityMap {
  const base = defaultWorkerAvailability()
  if (!raw || typeof raw !== "object") return base
  for (const d of WEEK_DAYS) {
    const row = raw[d]
    if (!row || typeof row !== "object") continue
    const o = row as Record<string, unknown>
    const e = o.earliest
    const l = o.latest
    const longer = o.visit_day_longer
    const vdl = o.visit_day_latest
    const pdm = o.preferred_duration_min
    let pref: number | undefined
    if (!("preferred_duration_min" in o)) {
      pref = base[d].preferred_duration_min
    } else if (pdm === null || pdm === "") {
      pref = undefined
    } else if (typeof pdm === "number" && Number.isFinite(pdm) && pdm > 0) {
      pref = Math.trunc(pdm)
    } else if (typeof pdm === "string" && /^\d+$/.test(pdm.trim())) {
      const n = Number(pdm.trim())
      if (n > 0) pref = n
    }
    base[d] = {
      earliest: typeof e === "string" && e.trim() ? e.trim() : base[d].earliest,
      latest: typeof l === "string" && l.trim() ? l.trim() : base[d].latest,
      visit_day_longer:
        typeof longer === "boolean" ? longer : base[d].visit_day_longer ?? false,
      visit_day_latest:
        typeof vdl === "string" && vdl.trim()
          ? vdl.trim()
          : (base[d].visit_day_latest ?? ""),
      preferred_duration_min: pref,
    }
  }
  return base
}
