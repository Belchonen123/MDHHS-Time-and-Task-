import { Keyboard } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

interface ShortcutsOverlayProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface ShortcutEntry {
  keys: string[]
  label: string
}

interface ShortcutSection {
  title: string
  items: ShortcutEntry[]
}

const SECTIONS: ShortcutSection[] = [
  {
    title: "Global",
    items: [
      { keys: ["⌘", "K"], label: "Open command palette" },
      { keys: ["⌘", "N"], label: "New plan (focus upload)" },
      { keys: ["⌘", "/"], label: "Show shortcut cheat sheet" },
      { keys: ["?"], label: "Show shortcut cheat sheet" },
      { keys: ["Esc"], label: "Close modals, cancel edits" },
    ],
  },
  {
    title: "Navigation",
    items: [
      { keys: ["⌘", "⇧", "C"], label: "Go to Clients" },
      {
        keys: ["⌘", "⇧", ","],
        label: "Go to Settings (plain ⌘/Ctrl+, also works if the browser does not capture it)",
      },
      { keys: ["↑", "↓"], label: "Navigate table rows" },
      { keys: ["Enter"], label: "Open focused row" },
    ],
  },
  {
    title: "Editor",
    items: [
      { keys: ["⌘", "Z"], label: "Undo" },
      { keys: ["⌘", "⇧", "Z"], label: "Redo" },
      { keys: ["⌘", "S"], label: "Save & rebuild" },
      { keys: ["Drag"], label: "Move task between days" },
      { keys: ["Click"], label: "Edit task minutes inline" },
    ],
  },
]

export function ShortcutsOverlay({ open, onOpenChange }: ShortcutsOverlayProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl p-0">
        <DialogHeader className="border-b border-neutral-200 px-6 py-4">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary-50">
              <Keyboard className="h-4 w-4 text-primary-700" />
            </div>
            <DialogTitle className="font-display text-lg font-semibold tracking-tight">
              Keyboard shortcuts
            </DialogTitle>
          </div>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-x-8 gap-y-5 px-6 py-6">
          {SECTIONS.map((s) => (
            <section key={s.title}>
              <h3 className="label-caps mb-2 text-[10px]">{s.title}</h3>
              <ul className="flex flex-col gap-1.5">
                {s.items.map((item, i) => (
                  <li
                    key={`${s.title}-${i}`}
                    className="flex items-center justify-between gap-3 text-sm text-neutral-700"
                  >
                    <span>{item.label}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {item.keys.map((k, j) => (
                        <kbd
                          key={j}
                          className={cn(
                            "rounded-sm border border-neutral-200 bg-neutral-50 px-1.5 py-0.5",
                            "font-mono text-[10px] text-neutral-600 shadow-xs",
                          )}
                        >
                          {k}
                        </kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <p className="border-t border-neutral-200 bg-neutral-50 px-6 py-3 text-xs text-neutral-500">
          Press{" "}
          <kbd className="rounded-sm border border-neutral-200 bg-white px-1 py-0.5 font-mono text-[10px]">
            Esc
          </kbd>{" "}
          or{" "}
          <kbd className="rounded-sm border border-neutral-200 bg-white px-1 py-0.5 font-mono text-[10px]">
            ?
          </kbd>{" "}
          to close.
        </p>
      </DialogContent>
    </Dialog>
  )
}

export default ShortcutsOverlay
