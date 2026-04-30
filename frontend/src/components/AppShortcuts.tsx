import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"

import { CommandPalette } from "@/components/CommandPalette"
import { ShortcutsOverlay } from "@/components/ShortcutsOverlay"
import { focusNewPlanUpload } from "@/lib/focusNewPlanUpload"

/**
 * Global keyboard shortcuts + command palette host.
 *
 * Mounted once at the app root (in `App.tsx`). Listens for:
 *
 *   ⌘/Ctrl+K          → open palette
 *   ⌘/Ctrl+N          → dispatch `app:focus-upload` (ClientList focuses the picker)
 *   ⌘/Ctrl+⇧+C        → go to /
 *   ⌘/Ctrl+,          → go to /settings (⌘/Ctrl+⇧+, if the browser steals plain comma)
 *   ⌘/Ctrl+/          → open cheat sheet
 *   ?                 → open cheat sheet (no-modifier)
 *   Esc               → closes palette / cheat sheet
 *
 * The palette and cheat sheet only mount their content when opened, so they're
 * free on the cold path.
 */
export function AppShortcuts() {
  const navigate = useNavigate()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [sheetOpen, setSheetOpen] = useState(false)

  const focusUpload = useCallback(() => {
    focusNewPlanUpload(navigate)
  }, [navigate])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const typing = isTypingTarget(e.target)

      // ⌘K — always wins, even while typing (except inside the palette
      // input itself which will have focus trapped by cmdk).
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPaletteOpen((o) => !o)
        return
      }

      if (typing) return

      if (mod && e.key.toLowerCase() === "n") {
        e.preventDefault()
        focusUpload()
      } else if (mod && e.shiftKey && e.key.toLowerCase() === "c") {
        e.preventDefault()
        navigate("/")
      } else if (mod && e.code === "Comma") {
        // Use `code` so ⌘/Ctrl+⇧+, works on US layouts (Shift+, produces "<", not ",").
        e.preventDefault()
        navigate("/settings")
      } else if (mod && e.key === "/") {
        e.preventDefault()
        setSheetOpen((o) => !o)
      } else if (!mod && e.key === "?") {
        e.preventDefault()
        setSheetOpen((o) => !o)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [focusUpload, navigate])

  return (
    <>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onOpenCheatSheet={() => {
          setPaletteOpen(false)
          setSheetOpen(true)
        }}
        onOpenUpload={focusUpload}
      />
      <ShortcutsOverlay open={sheetOpen} onOpenChange={setSheetOpen} />
    </>
  )
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true
  if (el.isContentEditable) return true
  return false
}

export default AppShortcuts
