import { useEffect, useState } from "react"
import { Command } from "cmdk"
import { useLocation, useNavigate } from "react-router-dom"
import {
  ArrowRight,
  FileCheck,
  HelpCircle,
  Keyboard,
  Plus,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  Users,
  type LucideIcon,
} from "lucide-react"

import { listClients } from "@/api/client"
import type { ClientSummary } from "@/types"
import { cn } from "@/lib/utils"

interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onOpenCheatSheet: () => void
  onOpenUpload: () => void
}

export function CommandPalette({
  open,
  onOpenChange,
  onOpenCheatSheet,
  onOpenUpload,
}: CommandPaletteProps) {
  const navigate = useNavigate()
  const { pathname } = useLocation()

  const [clients, setClients] = useState<ClientSummary[] | null>(null)

  // Lazily fetch clients the first time the palette opens, then cache for the
  // session. Refreshed each time the palette opens after a full close so
  // newly uploaded clients appear.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      try {
        const data = await listClients()
        if (!cancelled) setClients(data)
      } catch {
        if (!cancelled) setClients([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open])

  const close = () => onOpenChange(false)
  const go = (to: string) => {
    close()
    if (to !== pathname) navigate(to)
  }

  // --- detect current client id from URL so "Re-run current plan" is conditional
  const currentClientId = (() => {
    const m = pathname.match(/^\/clients\/([^/]+)/)
    return m ? decodeURIComponent(m[1]) : null
  })()

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command menu"
      overlayClassName={cn(
        "fixed inset-0 z-[65] bg-neutral-900/30 backdrop-blur-[2px]",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
      )}
      contentClassName={cn(
        "fixed left-1/2 top-24 z-[70] w-full max-w-xl -translate-x-1/2",
        "overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-xl",
        "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
      )}
    >
      <div>
        <div className="flex items-center gap-2 border-b border-neutral-200 px-3.5">
          <Search
            className="h-4 w-4 shrink-0 text-neutral-400"
            aria-hidden
          />
          <Command.Input
            placeholder="Type a command or search…"
            className={cn(
              "flex h-12 w-full bg-transparent text-sm outline-none",
              "placeholder:text-neutral-400",
            )}
            autoFocus
          />
          <kbd className="rounded-sm border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 font-mono text-[10px] text-neutral-500">
            Esc
          </kbd>
        </div>

        <Command.List className="max-h-[400px] overflow-y-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-sm text-neutral-500">
            No matching commands.
          </Command.Empty>

          <CmdGroup heading="Navigation">
            <CmdItem
              icon={Users}
              onSelect={() => go("/")}
              shortcut={["⌘", "⇧", "C"]}
            >
              Go to Clients
            </CmdItem>
            <CmdItem
              icon={SettingsIcon}
              onSelect={() => go("/settings")}
              shortcut={["⌘", "⇧", ","]}
            >
              Go to Settings
            </CmdItem>
          </CmdGroup>

          <CmdGroup heading="Actions">
            <CmdItem
              icon={Plus}
              onSelect={() => {
                close()
                onOpenUpload()
              }}
              shortcut={["⌘", "N"]}
            >
              New plan
            </CmdItem>
            {currentClientId && (
              <CmdItem
                icon={RefreshCw}
                onSelect={() => {
                  close()
                  // Delegate to ClientDetail via a DOM event — no tight
                  // coupling with its internal state.
                  window.dispatchEvent(new CustomEvent("app:rerun-current"))
                }}
                shortcut={["⌘", "R"]}
              >
                Re-run current plan
              </CmdItem>
            )}
            <CmdItem icon={Keyboard} onSelect={onOpenCheatSheet} shortcut={["?"]}>
              Keyboard shortcuts
            </CmdItem>
            <CmdItem icon={HelpCircle} onSelect={() => go("/")}>About this app</CmdItem>
          </CmdGroup>

          {clients && clients.length > 0 && (
            <CmdGroup heading={`Clients (${clients.length})`}>
              {clients.slice(0, 25).map((c) => (
                <CmdItem
                  key={c.client_id}
                  icon={FileCheck}
                  onSelect={() =>
                    go(`/clients/${encodeURIComponent(c.client_id)}`)
                  }
                  value={`${c.client_name} ${c.client_id}`}
                >
                  <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
                    <span className="truncate">
                      {c.client_name || c.client_id}
                    </span>
                    <span className="shrink-0 font-mono text-[11px] text-neutral-400">
                      {c.client_id}
                    </span>
                  </div>
                </CmdItem>
              ))}
            </CmdGroup>
          )}
        </Command.List>
      </div>
    </Command.Dialog>
  )
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

function CmdGroup({
  heading,
  children,
}: {
  heading: string
  children: React.ReactNode
}) {
  return (
    <Command.Group
      heading={heading}
      className={cn(
        "mb-1 [&_[cmdk-group-heading]]:label-caps [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2",
      )}
    >
      {children}
    </Command.Group>
  )
}

function CmdItem({
  icon: Icon,
  children,
  shortcut,
  onSelect,
  value,
}: {
  icon: LucideIcon
  children: React.ReactNode
  shortcut?: readonly string[]
  onSelect: () => void
  value?: string
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      value={value}
      className={cn(
        "flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm text-neutral-800",
        "data-[selected=true]:bg-primary-50 data-[selected=true]:text-primary-900",
        "transition-colors",
      )}
    >
      <Icon className="h-4 w-4 shrink-0 text-neutral-400" />
      <span className="flex-1">{children}</span>
      {shortcut && (
        <span className="flex items-center gap-0.5">
          {shortcut.map((k, i) => (
            <kbd
              key={i}
              className={cn(
                "rounded-sm border border-neutral-200 bg-neutral-50 px-1.5 py-0.5",
                "font-mono text-[10px] text-neutral-500",
              )}
            >
              {k}
            </kbd>
          ))}
        </span>
      )}
      <ArrowRight className="h-3 w-3 text-neutral-300 opacity-0 transition-opacity group-data-[selected=true]:opacity-100" />
    </Command.Item>
  )
}

export default CommandPalette
