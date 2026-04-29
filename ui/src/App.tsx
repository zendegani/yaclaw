import { createContext, useContext, useEffect, useRef } from "react";
import { Route, Routes } from "react-router-dom";
import { PishkarSocket } from "@/api/socket";
import { applyEvent, setStatus } from "@/state/store";
import { AppShell } from "@/layout/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { TracePage } from "@/pages/TracePage";
import { DashboardPage } from "@/pages/DashboardPage";
import { SettingsPage } from "@/pages/SettingsPage";

const USER_ID = "ali";
const SESSION_KEY = "pishkar.session_id";

function currentSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export function newSession(): void {
  localStorage.setItem(SESSION_KEY, crypto.randomUUID());
  window.location.reload();
}

export function getSessionId(): string {
  return currentSessionId();
}

const SocketContext = createContext<PishkarSocket | null>(null);

export function useSocket(): PishkarSocket {
  const sock = useContext(SocketContext);
  if (!sock) throw new Error("useSocket must be used inside <App>");
  return sock;
}

export function App() {
  const socketRef = useRef<PishkarSocket | null>(null);
  if (!socketRef.current) {
    socketRef.current = new PishkarSocket({
      userId: USER_ID,
      sessionId: currentSessionId(),
      onEvent: applyEvent,
      onStatus: setStatus,
    });
  }

  useEffect(() => {
    const sock = socketRef.current!;
    sock.connect();
    return () => sock.close();
  }, []);

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
