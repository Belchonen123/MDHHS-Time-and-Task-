/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional origin for API (no trailing slash), e.g. `http://127.0.0.1:8000`. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
