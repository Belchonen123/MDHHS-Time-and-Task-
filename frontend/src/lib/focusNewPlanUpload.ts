import type { NavigateFunction } from "react-router-dom"

/**
 * Go to the home page (UploadHero) and focus the PDF upload — scroll into view
 * and open the file picker when idle. Used by ⌘N, Command palette "New plan",
 * and the header "New Plan" button.
 */
export function focusNewPlanUpload(navigate: NavigateFunction) {
  if (window.location.pathname !== "/") {
    navigate("/")
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent("app:focus-upload"))
    }, 50)
  } else {
    window.dispatchEvent(new CustomEvent("app:focus-upload"))
  }
}
