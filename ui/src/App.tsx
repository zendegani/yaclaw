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
const SESSION_ID = "s-default";

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
      sessionId: SESSION_ID,
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
