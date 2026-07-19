/**
 * chat-store.ts — Store Zustand pour la conversation avec l'agent IA.
 *
 * Simple in-memory list of messages. Ne persiste pas encore. La persistance
 * (localStorage ou backend) viendra après (utile pour l'évaluation : garder
 * les traces des conversations pour analyse a posteriori).
 */

import { create } from "zustand";

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** Référence à une Proposal créée par ce message (si applicable). */
  proposalId?: string;
  createdAt: number;
}

interface ChatState {
  messages: ChatMessage[];
  isPending: boolean;
  isOpen: boolean;
  addMessage: (msg: Omit<ChatMessage, "id" | "createdAt">) => ChatMessage;
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
  setPending: (v) => set({ isPending: v }),
  setOpen: (v) => set({ isOpen: v }),
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  clear: () => set({ messages: [] }),
}));
