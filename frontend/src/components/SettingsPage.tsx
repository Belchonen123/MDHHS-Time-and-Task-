/**
 * Placeholder for future app settings — route exists so sidebar nav works.
 */
export function SettingsPage() {
  return (
    <div className="flex flex-col gap-2">
      <h1 className="font-display text-2xl font-semibold tracking-tight text-neutral-900">
        Settings
      </h1>
      <p className="max-w-xl text-sm text-neutral-600">
        There are no global settings in this build. Per-client data lives on each
        client page.
      </p>
    </div>
  )
}

export default SettingsPage
