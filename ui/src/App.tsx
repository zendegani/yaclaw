import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { PishkarSocket } from "@/api/socket";
import { applyEvent, setStatus } from "@/state/store";
import { AppShell } from "@/layout/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { TracePage } from "@/pages/TracePage";
import { DashboardPage } from "@/pages/DashboardPage";
import { SettingsPage } from "@/pages/SettingsPage";

const USER_ID = "user";
const SESSION_KEY = "pishkar.session_id";

async function resolveSessionId(): Promise<string> {
  // Honor an explicit local override (e.g. just hit "new session" → fresh uuid
  // stashed in localStorage). Otherwise ask the server for the latest session
  // for this user across any channel so phone-then-laptop continues a thread.
  const local = localStorage.getItem(SESSION_KEY);
  if (local) return local;
  try {
    const res = await fetch(`/sessions/latest/${USER_ID}`);
    if (res.ok) {
      const data = (await res.json()) as { session_id: string | null };
      if (data.session_id) {
        localStorage.setItem(SESSION_KEY, data.session_id);
        return data.session_id;
      }
    }
  } catch {
    // Server unreachable — fall through to a fresh uuid.
  }
  const fresh = crypto.randomUUID();
  localStorage.setItem(SESSION_KEY, fresh);
  return fresh;
}

export function newSession(): void {
  localStorage.setItem(SESSION_KEY, crypto.randomUUID());
  window.location.reload();
}

export function getSessionId(): string {
  return localStorage.getItem(SESSION_KEY) ?? "";
}

const SocketContext = createContext<PishkarSocket | null>(null);

export function useSocket(): PishkarSocket {
  const sock = useContext(SocketContext);
  if (!sock) throw new Error("useSocket must be used inside <App>");
  return sock;
}

export function App() {
  const socketRef = useRef<PishkarSocket | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    resolveSessionId().then((sessionId) => {
      if (cancelled) return;
      socketRef.current = new PishkarSocket({
        userId: USER_ID,
        sessionId,
        onEvent: applyEvent,
        onStatus: setStatus,
      });
      socketRef.current.connect();
      setReady(true);
    });
    return () => {
      cancelled = true;
      socketRef.current?.close();
    };
  }, []);

  if (!ready || !socketRef.current) return null;

  return (
    <SocketContext.Provider value={socketRef.current}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<ChatPage />} />
          <Route path="trace" element={<TracePage />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </SocketContext.Provider>
  );
}
