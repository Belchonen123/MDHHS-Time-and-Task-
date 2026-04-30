import { Component, type ErrorInfo, type ReactNode } from "react"
import { AlertOctagon, ChevronDown, RefreshCw, RotateCcw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface ErrorBoundaryProps {
  children: ReactNode
  /** Optional fallback renderer for custom error UI per-route. */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
  detailsOpen: boolean
}

/**
 * App-wide error boundary. Catches React render / effect errors and shows a
 * friendly recovery screen instead of a blank page. "Try again" remounts the
 * subtree (by resetting state), "Reload page" does a full reload.
 *
 * Nothing is sent anywhere — all logs stay in `console` (local-only).
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null, detailsOpen: false }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error, detailsOpen: false }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface in console so devs can find the actual stack. No telemetry.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info.componentStack)
  }

  reset = () => {
    this.setState({ error: null, detailsOpen: false })
  }

  reload = () => {
    window.location.reload()
  }

  toggleDetails = () => {
    this.setState((s) => ({ detailsOpen: !s.detailsOpen }))
  }

  render() {
    const { error, detailsOpen } = this.state
    if (!error) return this.props.children

    if (this.props.fallback) return this.props.fallback(error, this.reset)

    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center p-6">
        <div className="w-full max-w-lg rounded-xl border border-danger/20 bg-white p-8 shadow-md">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-danger-bg">
            <AlertOctagon
              className="h-7 w-7 text-danger"
              strokeWidth={2}
              aria-hidden
            />
          </div>

          <h2 className="font-display text-xl font-semibold tracking-tight text-neutral-900">
            Something broke
          </h2>
          <p className="mt-1.5 text-sm text-neutral-600">
            This shouldn&apos;t happen. The error has been logged locally (nothing
            sent anywhere). You can try again or reload the page.
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              onClick={this.reset}
              className="bg-primary-700 hover:bg-primary-800"
            >
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              Try again
            </Button>
            <Button type="button" variant="outline" onClick={this.reload}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              Reload page
            </Button>
          </div>

          <button
            type="button"
            onClick={this.toggleDetails}
            className={cn(
              "mt-5 inline-flex items-center gap-1 text-xs font-medium text-neutral-500",
              "transition-colors hover:text-neutral-800",
            )}
            aria-expanded={detailsOpen}
          >
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform",
                detailsOpen && "rotate-180",
              )}
            />
            {detailsOpen ? "Hide" : "Show"} details
          </button>

          {detailsOpen && (
            <pre
              className={cn(
                "mt-3 max-h-64 overflow-auto rounded-md bg-neutral-900 p-3",
                "font-mono text-[11px] leading-relaxed text-neutral-100",
              )}
            >
              {error.stack || `${error.name}: ${error.message}`}
            </pre>
          )}
        </div>
      </div>
    )
  }
}

export default ErrorBoundary
