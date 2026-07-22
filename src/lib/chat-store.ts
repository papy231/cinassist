/**
 * chat-store.ts — Store Zustand pour la conversation avec l'agent IA.
 *
 * Simple in-memory list of messages. Ne persiste pas encore. La persistance
 * (localStorage ou backend) viendra après (utile pour l'évaluation : garder
 * les traces des conversations pour analyse a posteriori).
 */

import { create } from "zustand";

export type ChatRole = "user" | "assistant" | "system";

/**
 * Un événement du trace ReAct pendant le streaming SSE. Miroir de l'event
 * backend `{type, step, name?, args?, content?}` — affiché live dans la bulle
 * assistant "isStreaming" pour donner du feedback pendant le raisonnement.
 */
export interface ChatStep {
  type: "thought" | "action" | "observation" | "done";
  step: number;
  name?: string;                            // action: tool name
  args?: Record<string, unknown>;
  content?: unknown;                        // thought: str · observation: obj/str
  meta?: { wall_s?: number; tokens?: number; tokens_per_s?: number };
}

/** Stats de latence/coût agrégées sur toute la trace ReAct pour affichage
 *  sous la bulle assistant. */
export interface ChatLatencyStats {
  totalWallSec: number;      // somme des wall_s de tous les events LLM
  totalTokens: number;       // somme des tokens LLM générés
  stepCount: number;         // nombre d'étapes ReAct (excl. done)
  toolCallCount: number;     // nombre d'actions (tool calls)
  elapsedSec: number;        // temps total depuis envoi user → done
}

/** Suggestion proactive attachée à un message assistant, ex. après ingest
 *  d'un clip. L'user clique → le `prompt` est envoyé à l'agent normal. */
export interface ChatProactiveSuggestion {
  title: string;
  description: string;
  prompt: string;
  icon?: string;   // key logique : users | scissors | volume-off | film | star
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** Référence à une Proposal créée par ce message (si applicable). */
  proposalId?: string;
  /** Trace ReAct en cours (streaming). Vidé/gardé selon UI. */
  steps?: ChatStep[];
  /** True tant que le stream SSE n'a pas émis "done". */
  isStreaming?: boolean;
  /** Stats latence/coût — attachées au done. */
  latency?: ChatLatencyStats;
  /** Suggestions cliquables (agent proactif après ingest). */
  proactive?: ChatProactiveSuggestion[];
  createdAt: number;
}

interface ChatState {
  messages: ChatMessage[];
  isPending: boolean;
  isOpen: boolean;
  addMessage: (msg: Omit<ChatMessage, "id" | "createdAt">) => ChatMessage;
  updateMessage: (id: string, patch: Partial<Omit<ChatMessage, "id" | "createdAt">>) => void;
  appendStep: (id: string, step: ChatStep) => void;
  setPending: (v: boolean) => void;
  setOpen: (v: boolean) => void;
  toggle: () => void;
  clear: () => void;
}

let msgCounter = 0;

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isPending: false,
  isOpen: false,
  addMessage: (input) => {
    const msg: ChatMessage = {
      ...input,
      id: `msg-${Date.now()}-${++msgCounter}`,
      createdAt: Date.now(),
    };
    set((s) => ({ messages: [...s.messages, msg] }));
    return msg;
  },
  updateMessage: (id, patch) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    })),
  appendStep: (id, step) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === id ? { ...m, steps: [...(m.steps ?? []), step] } : m,
      ),
    })),
  setPending: (v) => set({ isPending: v }),
  setOpen: (v) => set({ isOpen: v }),
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  clear: () => set({ messages: [] }),
}));
