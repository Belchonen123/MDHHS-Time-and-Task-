import { useEffect, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { AlertTriangle, RefreshCcw } from "lucide-react"

import { apiUrl } from "@/api/client"
import { cn } from "@/lib/utils"

const HEALTH_URL = apiUrl("/api/health")
/** Direct API base (not via Vite proxy) — for "open in new tab" diagnostics when the proxy path misbehaves. */
const DIRECT_HEALTH_URL = "http://127.0.0.1:8001/api/health"
const POLL_INTERVAL_MS = 15_000
// Aggressive retries while we know the server is down — come back faster.
const FAST_RETRY_MS = 3_000

/**
 * Top-of-viewport warning bar that appears when the backend health endpoint
 * is unreachable. The toaster handles per-request errors; this banner is
 * for the "wait, is it even running?" case.
 *
 * Calculations (PDF parse, schedule, validation, downloads) all call this API —
 * nothing can succeed while this banner is visible.
 */
export function BackendHealthBanner() {
  const [down, setDown] = useState(false)

  useEffect(() => {
    let cancelled = false
    let timer: number | null = null

    const check = async () => {
      if (cancelled) return
      try {
        const res = await fetch(HEALTH_URL, { cache: "no-store" })
        if (cancelled) return
        const unhealthy = !res.ok
        setDown(unhealthy)
        timer = window.setTimeout(
          check,
          unhealthy ? FAST_RETRY_MS : POLL_INTERVAL_MS,
        )
      } catch {
        if (cancelled) return
        setDown(true)
        timer = window.setTimeout(check, FAST_RETRY_MS)
      }
    }

    void check()

    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [])

  return (
    <AnimatePresence>
      {down && (
        <motion.div
          role="alert"
          initial={{ y: -8, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -8, opacity: 0 }}
          transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          className={cn(
            "sticky top-0 z-50 w-full",
            "border-b border-warning/30 bg-warning-bg text-warning",
          )}
        >
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-2 gap-y-1 px-6 py-2 text-xs">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span className="font-medium">
              Backend server not responding — PDF upload and calculations are disabled until the API answers.
            </span>
            <span className="min-w-0 text-warning/80">
              Start the API on{" "}
              <code className="rounded-sm bg-white/60 px-0.5 font-mono text-[11px]">
                127.0.0.1:8001
              </code>{" "}
              (from <code className="rounded-sm bg-white/60 px-0.5 font-mono text-[11px]">frontend/</code>:{" "}
              <code className="rounded-sm bg-white/60 px-1 py-0.5 font-mono text-[11px]">
                npm run dev
              </code>{" "}
              starts API + app; from repo root:{" "}
              <code className="rounded-sm bg-white/60 px-1 py-0.5 font-mono text-[11px]">
                npm run dev
              </code>{" "}
              or API only{" "}
              <code className="rounded-sm bg-white/60 px-1 py-0.5 font-mono text-[11px]">
                npm run dev:backend
              </code>
              ). Confirm{" "}
              <a
                href={DIRECT_HEALTH_URL}
                target="_blank"
                rel="noreferrer"
                className="underline decoration-warning/40 underline-offset-2 hover:decoration-warning"
              >
                {DIRECT_HEALTH_URL}
              </a>{" "}
              loads JSON — if not, Python/venv may be missing{" "}
              <code className="rounded-sm bg-white/60 px-0.5 font-mono">.venv</code>; see{" "}
              <code className="rounded-sm bg-white/60 px-0.5 font-mono">
                scripts/dev-backend.cjs
              </code>
              .
            </span>
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-warning/70">
              <RefreshCcw className="h-3 w-3 animate-spin [animation-duration:3s]" />
              Retrying…
            </span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default BackendHealthBanner
