/**
 * Fetch a URL as a blob and trigger a synthesized download.
 *
 * Avoids `<a download target="_blank">` because on Chromium with
 * "Ask where to save each file", the tab can close before the save
 * dialog completes. Fetch-then-blob keeps the download in-page and
 * allows callers to surface HTTP errors.
 */

/** Short copy for toast `description` when `fetch` never reaches the API. */
export const DOWNLOAD_NETWORK_TOAST_DESCRIPTION =
  "From the project root run npm run dev, then open http://localhost:3456 (Vite). " +
  "That starts the API (default port 8001 via npm run dev:backend) and proxies /api for downloads. " +
  "For npm run preview after a build, keep the API running; preview must proxy /api. " +
  "Or set VITE_API_BASE_URL=http://127.0.0.1:8001 and rebuild if the UI is hosted without a proxy."

const DOWNLOAD_FETCH_HINT =
  "Could not reach the server. " +
  DOWNLOAD_NETWORK_TOAST_DESCRIPTION +
  " Manual start: python -m uvicorn main:app --host 127.0.0.1 --port 8001 from the backend folder."

/** True when `downloadBlobFromUrl` failed at the network layer (browser `fetch` threw). */
export function isLikelyDownloadNetworkFailure(message: string): boolean {
  const m = message.trim().toLowerCase()
  return (
    m.includes("failed to fetch") ||
    m.includes("could not reach the server") ||
    m.includes("networkerror") ||
    m.includes("network request failed") ||
    m.includes("load failed") ||
    m.includes("connection refused") ||
    m.includes("err_connection_refused") ||
    m.includes("ecconrefused") ||
    m.startsWith("network error")
  )
}

export async function downloadBlobFromUrl(
  url: string,
  suggestedName: string,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(url, { credentials: "same-origin" })
  } catch (e) {
    const base = e instanceof Error ? e.message : "Network error"
    throw new Error(`${base} — ${DOWNLOAD_FETCH_HINT}`)
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const blobUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement("a")
    a.href = blobUrl
    a.download = suggestedName
    a.rel = "noopener"
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  }
}
