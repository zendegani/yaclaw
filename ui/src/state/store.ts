import { useSyncExternalStore } from "react";
import type { Event, ToolUseBlock } from "../api/events";

export interface AssistantTurn {
  turn_id: string;
  text: string;
  thinking: string;
  toolCalls: Record<string, { use: ToolUseBlock; result?: string; isError?: boolean }>;
  toolOrder: string[];
  done: boolean;
}

export interface UserMessage {
  kind: "user";
  content: string;
  ts: number;
}

export interface AssistantMessage {
  kind: "assistant";
  turn: AssistantTurn;
}

export type ChatItem = UserMessage | AssistantMessage;

interface State {
  items: ChatItem[];
  status: "connecting" | "open" | "closed";
  pendingApproval: { tool: string; input: Record<string, unknown> } | null;
}

let state: State = { items: [], status: "connecting", pendingApproval: null };
const listeners = new Set<() => void>();

function setState(next: State): void {
  state = next;
  listeners.forEach((l) => l());
}

function findOrCreateTurn(items: ChatItem[], turn_id: string): [ChatItem[], AssistantTurn] {
  const last = items[items.length - 1];
  if (last && last.kind === "assistant" && last.turn.turn_id === turn_id) {
    return [items, last.turn];
  }
  const turn: AssistantTurn = {
    turn_id,
    text: "",
    thinking: "",
    toolCalls: {},
    toolOrder: [],
    done: false,
  };
  return [[...items, { kind: "assistant", turn }], turn];
}

export function applyEvent(event: Event): void {
  const items = state.items;
  switch (event.type) {
    case "turn_start": {
      const [next] = findOrCreateTurn(items, event.turn_id);
      setState({ ...state, items: next });
      return;
    }
    case "content_block_start": {
      if (event.content_block.type === "tool_use") {
        const block = event.content_block;
        const [next, turn] = findOrCreateTurn(items, event.turn_id);
        if (!turn.toolCalls[block.id]) {
          turn.toolCalls[block.id] = { use: block };
          turn.toolOrder.push(block.id);
        }
        setState({ ...state, items: [...next] });
      }
      return;
    }
    case "content_block_delta": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      if (event.delta.type === "text_delta") turn.text += event.delta.text;
      else if (event.delta.type === "thinking_delta") turn.thinking += event.delta.thinking;
      setState({ ...state, items: [...next] });
      return;
    }
    case "tool_result": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      const slot = turn.toolCalls[event.tool_use_id];
      if (slot) {
        slot.result = event.content;
        slot.isError = event.is_error;
      }
      setState({ ...state, items: [...next] });
      return;
    }
    case "turn_end": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      turn.done = true;
      setState({ ...state, items: [...next] });
      return;
    }
    default:
      return;
  }
}

export function appendUserMessage(content: string): void {
  setState({ ...state, items: [...state.items, { kind: "user", content, ts: Date.now() }] });
}

export function setStatus(status: State["status"]): void {
  setState({ ...state, status });
}

export function setPendingApproval(p: State["pendingApproval"]): void {
  setState({ ...state, pendingApproval: p });
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useChatState(): State {
  return useSyncExternalStore(subscribe, () => state, () => state);
}
