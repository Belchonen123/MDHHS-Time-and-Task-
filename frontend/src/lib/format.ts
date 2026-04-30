/**
 * Shared number / duration / currency formatters.
 *
 * Using a single module means "$1,911.60" always looks the same in the banner,
 * the stats strip, the reconciliation table, and the downloads page.
 */

const moneyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const intFmt = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
})

const num1Fmt = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
})

export function formatMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return moneyFmt.format(v)
}

export function formatSignedMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  const sign = v > 0 ? "+" : v < 0 ? "−" : ""
  return `${sign}${moneyFmt.format(Math.abs(v))}`
}

export function formatInt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return intFmt.format(v)
}

export function formatNumber1(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—"
  return num1Fmt.format(v)
}

/** 4248 -> "70:48" (HH:MM; matches printed MDHHS-6064-P). */
export function formatHoursMinutes(totalMinutes: number | null | undefined): string {
  if (totalMinutes == null || Number.isNaN(totalMinutes)) return "—"
  const m = Math.round(totalMinutes)
  if (m === 0) return "—"
  const sign = m < 0 ? "-" : ""
  const abs = Math.abs(m)
  const h = Math.floor(abs / 60)
  const rem = abs % 60
  return `${sign}${String(h).padStart(2, "0")}:${String(rem).padStart(2, "0")}`
}
