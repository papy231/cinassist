/**
 * proposals.ts — Store Zustand pour les Proposals.
 *
 * Une Proposal est une intention d'édition (batch de TimelineCmd) créée par
 * l'agent IA ou un pipeline déterministe. L'utilisateur l'accepte/refuse via
 * l'UI. Le store maintient la liste et son état.
 *
 * Golden rule : le store ne SAIT PAS appliquer une proposal. C'est l'Editor
 * qui expose un `TimelineCommandExecutor` (via useTimelineExecutorRegister)
 * et le store le consomme quand `acceptProposal` est appelé.
 */

import { create } from "zustand";
import type { Proposal, TimelineCmd, TimelineCommandExecutor } from "./timeline-commands";

let proposalCounter = 0;

interface ProposalState {
  proposals: Proposal[];
  /** Executor branché par Editor.tsx via `registerExecutor`. Null si Editor
   *  pas monté. Le store ne peut rien appliquer sans lui. */
  executor: TimelineCommandExecutor | null;

  registerExecutor: (ex: TimelineCommandExecutor | null) => void;

  addProposal: (input: Omit<Proposal, "id" | "createdAt" | "status">) => Proposal;
  /** Wendet die Proposal PROBEWEISE an (Timeline spielt sie, NICHTS gespeichert). Annehmen behält,
   *  Ablehnen/Verwerfen stellt den vorherigen Zustand exakt wieder her. Nur eine Vorschau gleichzeitig. */
  previewProposal: (id: string) => void;
  acceptProposal: (id: string) => void;
  rejectProposal: (id: string) => void;
  removeProposal: (id: string) => void;
  clear: () => void;

  getPending: () => Proposal[];
}

let previewBackup: unknown = null;          // Timeline-Zustand VOR der laufenden Vorschau (opak, vom Executor)
let previewingId: string | null = null;

export const useProposalStore = create<ProposalState>((set, get) => ({
  proposals: [],
  executor: null,

  registerExecutor: (ex) => set({ executor: ex }),

  addProposal: (input) => {
    const proposal: Proposal = {
      ...input,
      id: `prop-${Date.now()}-${++proposalCounter}`,
      status: "pending",
      createdAt: Date.now(),
    };
    set((s) => ({ proposals: [...s.proposals, proposal] }));
    return proposal;
  },

  previewProposal: (id) => {
    const { proposals, executor } = get();
    const p = proposals.find((x) => x.id === id);
    if (!p || p.status !== "pending" || !executor) return;
    // Eine andere Vorschau läuft? → erst exakt zurückstellen (wieder pending)
    if (previewingId && previewingId !== id && previewBackup != null) {
      executor.restoreState(previewBackup);
      const altId = previewingId;
      set((s) => ({ proposals: s.proposals.map((x) => (x.id === altId ? { ...x, status: "pending" } : x)) }));
    }
    previewBackup = executor.captureState();
    previewingId = id;
    executor.executeBatch(p.edits, `${p.title} (Vorschau)`);
    set((s) => ({ proposals: s.proposals.map((x) => (x.id === id ? { ...x, status: "previewing" } : x)) }));
  },

  acceptProposal: (id) => {
    const { proposals, executor } = get();
    const p = proposals.find((x) => x.id === id);
    if (!p) return;
    const istSequenz = p.edits.some((e) => e.type === "loadSequence" && e.replace);
    // Erst ANNEHMEN speichert eine Sequenz als Fassung im Backend — Vorschau/pending nie. Verzögert,
    // damit React den angewandten Zustand gerendert hat und der frisch registrierte Executor ihn sieht.
    const speichern = () => {
      if (istSequenz) setTimeout(() => get().executor?.persist?.(p.title), 450);
    };
    if (p.status === "previewing") {
      // Vorschau läuft bereits auf der Timeline → nur festschreiben
      previewBackup = null;
      previewingId = null;
      set((s) => ({ proposals: s.proposals.map((x) => (x.id === id ? { ...x, status: "accepted" } : x)) }));
      speichern();
      return;
    }
    if (p.status !== "pending") return;
    if (!executor) {
      console.warn("[proposals] no executor registered, cannot apply proposal", id);
      return;
    }
    executor.executeBatch(p.edits, p.title);
    set((s) => ({
      proposals: s.proposals.map((x) => (x.id === id ? { ...x, status: "accepted" } : x)),
    }));
    speichern();
  },

  rejectProposal: (id) => {
    const { proposals, executor } = get();
    const p = proposals.find((x) => x.id === id);
    if (p && p.status === "previewing" && executor && previewBackup != null) {
      executor.restoreState(previewBackup);      // Timeline exakt zurück auf den Stand vor der Vorschau
      previewBackup = null;
      previewingId = null;
    }
    set((s) => ({
      proposals: s.proposals.map((x) => (x.id === id ? { ...x, status: "rejected" } : x)),
    }));
  },

  removeProposal: (id) =>
    set((s) => ({
      proposals: s.proposals.filter((x) => x.id !== id),
    })),

  clear: () => set({ proposals: [] }),

  getPending: () => get().proposals.filter((p) => p.status === "pending"),
}));

/**
 * Helper pour l'agent (côté serveur / worker future) qui veut push une proposal
 * complète. À wrapper dans un appel HTTP quand le backend sera branché.
 */
export function addProposalFromAgent(input: {
  title: string;
  summary?: string;
  edits: TimelineCmd[];
  tool: string;
  params?: Record<string, unknown>;
  agentThought?: string;
}): Proposal {
  return useProposalStore.getState().addProposal({
    title: input.title,
    summary: input.summary,
    edits: input.edits,
    createdBy: "agent",
    provenance: {
      tool: input.tool,
      params: input.params,
      agentThought: input.agentThought,
    },
  });
}
