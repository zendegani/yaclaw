import { useEffect, useRef } from "react";
import { Chat } from "./components/Chat";
import { PishkarSocket } from "./api/socket";
import { applyEvent, setStatus } from "./state/store";
import "./app.css";

const USER_ID = "ali";
const SESSION_ID = "s-default";

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

  return <Chat socket={socketRef.current!} />;
}
