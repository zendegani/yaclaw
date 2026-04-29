import { useSyncExternalStore } from "react";
import type { Event, ToolUseBlock } from "@/api/events";

export interface AssistantTurn {
  turn_id: string;
  text: string;
  thinking: string;
  toolCalls: Record<string, { use: ToolUseBlock; result?: string; isError?: boolean }>;
  toolOrder: string[];
  done: boolean;
  stopReason?: string;
  inputTokens?: number;
  outputTokens?: number;
  model?: string;
  startedAt: number;
  endedAt?: number;
}

export interface UserMessage { kind: "user"; content: string; ts: number }
export interface AssistantMessage { kind: "assistant"; turn: AssistantTurn }
export type ChatItem = UserMessage | AssistantMessage;

export interface RawEntry {
  ts: number;
  event: Event;
}

interface State {
  items: ChatItem[];
  status: "connecting" | "open" | "closed";
  raw: RawEntry[];
  totals: { inputTokens: number; outputTokens: number; turns: number };
}

let state: State = {
  items: [],
  status: "connecting",
  raw: [],
  totals: { inputTokens: 0, outputTokens: 0, turns: 0 },
};
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
    startedAt: Date.now(),
  };
  return [[...items, { kind: "assistant", turn }], turn];
}

const RAW_LIMIT = 1000;

export function applyEvent(event: Event): void {
  const items = state.items;
  const raw = [...state.raw, { ts: Date.now(), event }].slice(-RAW_LIMIT);
  let totals = state.totals;

  switch (event.type) {
    case "turn_start": {
      const [next] = findOrCreateTurn(items, event.turn_id);
      setState({ ...state, items: next, raw, totals });
      return;
    }
    case "message_start": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      turn.model = event.model;
      setState({ ...state, items: [...next], raw, totals });
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
        setState({ ...state, items: [...next], raw, totals });
      } else {
        setState({ ...state, raw, totals });
      }
      return;
    }
    case "content_block_delta": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      if (event.delta.type === "text_delta") turn.text += event.delta.text;
      else if (event.delta.type === "thinking_delta") turn.thinking += event.delta.thinking;
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "tool_result": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      const slot = turn.toolCalls[event.tool_use_id];
      if (slot) {
        slot.result = event.content;
        slot.isError = event.is_error;
      }
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "message_delta": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      if (event.input_tokens != null) turn.inputTokens = (turn.inputTokens ?? 0) + event.input_tokens;
      if (event.output_tokens != null) turn.outputTokens = (turn.outputTokens ?? 0) + event.output_tokens;
      totals = {
        inputTokens: totals.inputTokens + (event.input_tokens ?? 0),
        outputTokens: totals.outputTokens + (event.output_tokens ?? 0),
        turns: totals.turns,
      };
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    case "turn_end": {
      const [next, turn] = findOrCreateTurn(items, event.turn_id);
      turn.done = true;
      turn.stopReason = event.stop_reason;
      turn.endedAt = Date.now();
      totals = { ...totals, turns: totals.turns + 1 };
      setState({ ...state, items: [...next], raw, totals });
      return;
    }
    default:
      setState({ ...state, raw, totals });
      return;
  }
}

export function appendUserMessage(content: string): void {
  setState({ ...state, items: [...state.items, { kind: "user", content, ts: Date.now() }] });
}

export function setStatus(status: State["status"]): void {
  setState({ ...state, status });
}

export function clearRaw(): void {
  setState({ ...state, raw: [] });
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useChatState(): State {
  return useSyncExternalStore(subscribe, () => state, () => state);
}
