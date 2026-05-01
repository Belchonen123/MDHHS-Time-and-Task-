import type { DownloadFileType } from "@/api/client"
import type { DownloadArtifact, UploadResult } from "@/types"

const PREFIX = "mdhhs:upload-artifacts"

function storageKey(clientId: string, version: number): string {
  return `${PREFIX}:${clientId}:v${version}`
}

function artifactToBlob(artifact: DownloadArtifact): Blob {
  const binary = window.atob(artifact.base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return new Blob([bytes], { type: artifact.media_type })
}

export function storeUploadArtifacts(result: UploadResult): void {
  if (!result.artifacts || Object.keys(result.artifacts).length === 0) return
  try {
    window.sessionStorage.setItem(
      storageKey(result.client.client_id, result.plan.version),
      JSON.stringify(result.artifacts),
    )
  } catch {
    // Best-effort only; normal API downloads remain the primary path.
  }
}

export function downloadStoredArtifact(
  clientId: string,
  version: number,
  filetype: DownloadFileType,
  fallbackFilename: string,
): boolean {
  try {
    const raw = window.sessionStorage.getItem(storageKey(clientId, version))
    if (!raw) return false
    const artifacts = JSON.parse(raw) as Partial<Record<DownloadFileType, DownloadArtifact>>
    const artifact = artifacts[filetype]
    if (!artifact?.base64) return false
    const url = window.URL.createObjectURL(artifactToBlob(artifact))
    const a = document.createElement("a")
    a.href = url
    a.download = artifact.filename || fallbackFilename
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => window.URL.revokeObjectURL(url), 1000)
    return true
  } catch {
    return false
  }
}
