import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { AnimatePresence, motion } from "framer-motion"
import {
  ChevronRight,
  FileCheck,
  History,
  Plus,
  Settings as SettingsIcon,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react"

import { focusNewPlanUpload } from "@/lib/focusNewPlanUpload"
import { slideUp } from "@/lib/motion"
import { cn } from "@/lib/utils"

// ===========================================================================
// Breadcrumb context
//
// Pages can override the label for a given path segment once they've loaded
// a friendlier name (e.g. swap the raw client id for the client's full name):
//
//     const { setLabel } = useBreadcrumb()
//     useEffect(() => {
//       if (client) setLabel(`/clients/${client.client_id}`, client.client_name)
//     }, [client])
// ===========================================================================

type BreadcrumbCtx = {
  labels: Record<string, string>
  setLabel: (path: string, label: string) => void
  clearLabel: (path: string) => void
}

const BreadcrumbContext = createContext<BreadcrumbCtx | null>(null)

export function useBreadcrumb(): BreadcrumbCtx {
  const ctx = useContext(BreadcrumbContext)
  if (!ctx) throw new Error("useBreadcrumb must be used within <AppShell>")
  return ctx
}

/**
 * Convenience hook — registers a label for the given path for the lifetime
 * of the component.
 */
export function useBreadcrumbLabel(path: string | null | undefined, label: string | null | undefined) {
  const { setLabel, clearLabel } = useBreadcrumb()
  useEffect(() => {
    if (!path || !label) return
    setLabel(path, label)
    return () => clearLabel(path)
  }, [path, label, setLabel, clearLabel])
}

// ===========================================================================
// Breadcrumb
// ===========================================================================

type Crumb = { label: string; to: string | null }

const STATIC_LABELS: Record<string, string> = {
  "": "Home",
  clients: "Clients",
  plans: "Plans",
  edit: "Edit",
  history: "History",
  settings: "Settings",
}

function buildCrumbs(pathname: string, overrides: Record<string, string>): Crumb[] {
  const parts = pathname.split("/").filter(Boolean)
  const crumbs: Crumb[] = [{ label: "Home", to: "/" }]

  let acc = ""
  for (let i = 0; i < parts.length; i++) {
    const segment = parts[i]
    acc += `/${segment}`

    const overridden = overrides[acc]
    const staticLabel = STATIC_LABELS[segment]

    let label: string
    if (overridden) {
      label = overridden
    } else if (staticLabel) {
      label = staticLabel
    } else {
      // Dynamic segment (id, version, …). Prefer a friendly fallback.
      const prev = parts[i - 1]
      if (prev === "clients") label = "Client"
      else if (prev === "plans") label = `v${segment}`
      else label = decodeURIComponent(segment)
    }

    const isLast = i === parts.length - 1
    crumbs.push({ label, to: isLast ? null : acc })
  }

  return crumbs
}

function Breadcrumb({ labels }: { labels: Record<string, string> }) {
  const { pathname } = useLocation()
  const crumbs = useMemo(() => buildCrumbs(pathname, labels), [pathname, labels])

  return (
    <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
      <ol className="flex items-center gap-1.5 text-sm text-neutral-500">
        {crumbs.map((c, i) => {
          const isLast = i === crumbs.length - 1
          return (
            <li key={i} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-neutral-400" />}
              {c.to && !isLast ? (
                <Link
                  to={c.to}
                  className="truncate rounded px-1 text-primary-700 transition-colors hover:text-primary-900"
                >
                  {c.label}
                </Link>
              ) : (
                <span
                  className={cn(
                    "truncate px-1",
                    isLast ? "font-semibold text-neutral-900" : "text-neutral-500",
                  )}
                  aria-current={isLast ? "page" : undefined}
                >
                  {c.label}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

// ===========================================================================
// Top bar
// ===========================================================================

function TopBar({ labels }: { labels: Record<string, string> }) {
  const navigate = useNavigate()
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-6 border-b border-neutral-200 bg-white px-6">
      {/* Brand */}
      <Link to="/" className="flex shrink-0 items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary-700 text-white shadow-sm">
          <FileCheck className="h-4 w-4" />
        </div>
        <span className="font-display text-lg font-semibold tracking-tight text-neutral-900">
          MDHHS Plan Builder
        </span>
      </Link>

      <div className="h-6 w-px shrink-0 bg-neutral-200" aria-hidden="true" />

      <Breadcrumb labels={labels} />

      {/* Primary action */}
      <button
        type="button"
        onClick={() => focusNewPlanUpload(navigate)}
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary-700 px-4 py-2",
          "text-sm font-medium text-white shadow-sm",
          "transition-[background-color,box-shadow] duration-[160ms] ease-out",
          "hover:bg-primary-800 hover:shadow-md",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-2",
        )}
      >
        <Plus className="h-4 w-4" />
        New Plan
      </button>
    </header>
  )
}

// ===========================================================================
// Sidebar
// ===========================================================================

type NavEntry = {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const NAV_SECTIONS: Array<{ label: string; items: NavEntry[] }> = [
  {
    label: "Workspace",
    items: [
      { to: "/", label: "Clients", icon: Users, end: true },
      { to: "/history", label: "History", icon: History },
      { to: "/settings", label: "Settings", icon: SettingsIcon },
    ],
  },
]

function Sidebar() {
  return (
    <aside className="sticky top-16 flex h-[calc(100dvh-4rem)] w-60 shrink-0 flex-col border-r border-neutral-200 bg-white">
      <nav className="flex-1 overflow-y-auto py-4">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="mb-2">
            <div className="label-caps px-4 py-2">{section.label}</div>
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      cn(
                        "group relative mx-2 flex items-center gap-2.5 rounded-lg px-4 py-2.5 text-sm",
                        "transition-colors duration-150",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-700 focus-visible:ring-offset-1",
                        isActive
                          ? "font-medium text-primary-800"
                          : "text-neutral-700 hover:bg-neutral-100",
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && (
                          <motion.span
                            layoutId="sidebar-active-pill"
                            className="absolute inset-0 rounded-lg bg-primary-50"
                            transition={{
                              type: "spring",
                              stiffness: 420,
                              damping: 34,
                            }}
                            aria-hidden
                          />
                        )}
                        <item.icon className="relative z-10 h-4 w-4 shrink-0" />
                        <span className="relative z-10 truncate">
                          {item.label}
                        </span>
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* Privacy status chip */}
      <div className="border-t border-neutral-200 p-4">
        <div
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1",
            "border-green-200 bg-green-50 text-green-700",
            "text-[11px] font-medium",
          )}
          style={{ letterSpacing: "0.01em" }}
        >
          <ShieldCheck className="h-3 w-3" />
          <span>localhost only • no PHI transmitted</span>
        </div>
      </div>
    </aside>
  )
}

// ===========================================================================
// Content area with animated Outlet
// ===========================================================================

function AnimatedOutlet() {
  const { pathname } = useLocation()
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        initial={slideUp.initial}
        animate={slideUp.animate}
        exit={slideUp.exit}
        transition={slideUp.transition}
        className="w-full"
      >
        <Outlet />
      </motion.div>
    </AnimatePresence>
  )
}

// ===========================================================================
// AppShell — public component
// ===========================================================================

export function AppShell({ children }: { children?: ReactNode }) {
  const [labels, setLabels] = useState<Record<string, string>>({})
  const setLabel = useCallback((path: string, label: string) => {
    setLabels((prev) => (prev[path] === label ? prev : { ...prev, [path]: label }))
  }, [])
  const clearLabel = useCallback((path: string) => {
    setLabels((prev) => {
      if (!(path in prev)) return prev
      const next = { ...prev }
      delete next[path]
      return next
    })
  }, [])
  const ctx = useMemo<BreadcrumbCtx>(
    () => ({ labels, setLabel, clearLabel }),
    [labels, setLabel, clearLabel],
  )

  return (
    <BreadcrumbContext.Provider value={ctx}>
      <div className="flex min-h-dvh flex-col bg-neutral-50">
        <TopBar labels={labels} />
        <div className="flex flex-1">
          <Sidebar />
          <main className="flex-1">
            <div className="mx-auto w-full max-w-7xl px-8 py-6">
              {children ?? <AnimatedOutlet />}
            </div>
          </main>
        </div>
      </div>
    </BreadcrumbContext.Provider>
  )
}

export default AppShell
