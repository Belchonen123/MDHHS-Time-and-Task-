import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import App from "./App"
import { Toaster } from "@/components/ui/sonner"

// Variable fonts — loaded locally via Fontsource so we don't depend on
// Google Fonts at runtime. The variable builds ship a single file covering
// all weights, which keeps the bundle small.
import "@fontsource-variable/inter/index.css"
import "@fontsource-variable/jetbrains-mono/index.css"

import "sonner/dist/styles.css"
import "./index.css"

const el = document.getElementById("root")
if (!el) {
  throw new Error("Root element not found")
}

createRoot(el).render(
  <StrictMode>
    <BrowserRouter>
      <App />
      <Toaster />
    </BrowserRouter>
  </StrictMode>
)
