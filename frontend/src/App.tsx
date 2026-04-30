import { Route, Routes } from "react-router-dom"

import { AppShortcuts } from "@/components/AppShortcuts"
import { BackendHealthBanner } from "@/components/BackendHealthBanner"
import { ClientDetail } from "@/components/ClientDetail"
import { ClientList } from "@/components/ClientList"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { ScheduleEditor } from "@/components/ScheduleEditor"
import { SettingsPage } from "@/components/SettingsPage"
import { WorkspaceHistory } from "@/components/WorkspaceHistory"
import { AppShell } from "@/components/layout/AppShell"

export default function App() {
  return (
    <ErrorBoundary>
      <BackendHealthBanner />
      <AppShortcuts />
      <Routes>
        {/* Immersive editor — no AppShell chrome so the three-column workspace
         *  can use every pixel. */}
        <Route
          path="/clients/:id/plans/:version/edit"
          element={<ScheduleEditor />}
        />
        <Route element={<AppShell />}>
          {/* Home */}
          <Route index element={<ClientList />} />
          {/* Explicit /clients (+ optional trailing slash) — same listing; avoids blank screen */}
          <Route path="clients" element={<ClientList />} />
          <Route path="history" element={<WorkspaceHistory />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="clients/:id" element={<ClientDetail />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}
