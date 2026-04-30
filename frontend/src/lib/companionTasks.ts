// Mirror backend COMPANION_TO_PARENT (`backend/app/calculate.py`).
export const COMPANION_TO_PARENT: Record<string, string> = {
  "Travel For Shopping": "Shopping for Food/Meds",
  "Travel For Laundry": "Laundry",
}

export function isCompanionTask(name: string): boolean {
  return name in COMPANION_TO_PARENT
}

export function parentOf(name: string): string | undefined {
  return COMPANION_TO_PARENT[name]
}
