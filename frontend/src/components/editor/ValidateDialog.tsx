import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ValidationPanel } from "@/components/ValidationPanel"
import type { ValidationReport } from "@/types"

interface ValidateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  report: ValidationReport | null
  validating: boolean
  onSaveAnyway: () => void
  onKeepEditing: () => void
}

export function ValidateDialog({
  open,
  onOpenChange,
  report,
  validating,
  onSaveAnyway,
  onKeepEditing,
}: ValidateDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-hidden p-0">
        <DialogHeader className="border-b border-neutral-200 px-6 py-4">
          <DialogTitle className="font-display text-lg font-semibold tracking-tight">
            Validation preview
          </DialogTitle>
          <p className="text-sm text-neutral-600">
            Ran on the current draft — not yet saved.
          </p>
        </DialogHeader>

        <div className="max-h-[60vh] overflow-y-auto p-6">
          {validating || !report ? (
            <div className="flex items-center justify-center py-12 text-neutral-400">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : (
            <ValidationPanel report={report} />
          )}
        </div>

        <DialogFooter className="border-t border-neutral-200 px-6 py-4">
          <Button type="button" variant="outline" onClick={onKeepEditing}>
            Keep editing
          </Button>
          <Button
            type="button"
            onClick={onSaveAnyway}
            disabled={validating}
            className="bg-primary-700 hover:bg-primary-800"
          >
            Save anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default ValidateDialog
