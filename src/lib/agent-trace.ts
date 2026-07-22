/**
 * agent-trace.ts — Convertit une trace ReAct (events du backend agent) en
 * proposals frontend prêts à être poussés dans le ProposalStore.
 *
 * Utilisé côté serveur (/api/agent/chat sync) ET client (ChatPanel après
 * stream SSE complet). Logique unique = comportement identique.
 */

import type { TimelineCmd } from "@/lib/timeline-commands";

export type BackendTraceEvent = {
  type: "thought" | "action" | "observation" | "done";
  step: number;
  name?: string;
  args?: Record<string, unknown>;
  content?: unknown;
  // Metadata de latence/coût par event (émis par le backend agent). Utilisé
  // pour agréger les badges "12s · 4200 tokens" sous la réponse finale.
  meta?: {
    wall_s?: number;         // temps wall-clock du call LLM/tool en secondes
    tokens?: number;         // tokens générés par le LLM à ce tour
    tokens_per_s?: number;   // débit
  };
};

export type ProposalPayload = {
  title: string;
  summary?: string;
  edits: TimelineCmd[];
  provenance?: {
    tool: string;
    params?: Record<string, unknown>;
    agentThought?: string;
  };
};

export type SnapshotClip = {
  tlId: string;
  clipId: string;
  name?: string;
  start: number;
  duration: number;
  mediaStart: number;
  videoTrackIndex?: number;
  audioTrackIndex?: number;
  hasAudio?: boolean;
};

export type SnapshotState = {
  totalDuration: number;
  fps: number;
  numVideoTracks: number;
  numAudioTracks: number;
  clips: SnapshotClip[];
};

/**
 * Convertit la trace en proposals. Actuellement supporte les observations avec
 * un champ `silences` (produites par `remove_silences`, `find_hesitations`) :
 *   (a) groupe par clip_name
 *   (b) trouve les tlClips correspondants (name/clipId, avec/sans extension)
 *   (c) traduit les positions source → timeline
 *   (d) émet un deleteRange ripple par silence.
 */
export function traceToProposals(
  trace: BackendTraceEvent[],
  timelineState?: SnapshotState,
): ProposalPayload[] {
  const proposals: ProposalPayload[] = [];
  const tlClips = timelineState?.clips ?? [];

  for (let i = 0; i < trace.length; i++) {
    const evt = trace[i];
    if (evt.type !== "observation") continue;
    const obs = evt.content as Record<string, unknown> | null;
    if (!obs || typeof obs !== "object") continue;
    const actionEvt = [...trace.slice(0, i)].reverse().find((e) => e.type === "action");
    const toolName = actionEvt?.name ?? "unknown";
    const thoughtEvt = [...trace.slice(0, i)].reverse().find((e) => e.type === "thought");
    const agentThought = typeof thoughtEvt?.content === "string" ? thoughtEvt.content : undefined;

    const silencesRaw = obs.silences as unknown;
    if (!Array.isArray(silencesRaw) || silencesRaw.length === 0) continue;

    const bySource: Record<string, Array<{ mediaStart: number; duration: number }>> = {};
    for (const raw of silencesRaw) {
      if (typeof raw !== "object" || raw === null) continue;
      const s = raw as { clip_name?: string; media_start?: number; duration?: number };
      if (typeof s.media_start !== "number" || typeof s.duration !== "number") continue;
      const key = s.clip_name ?? "unknown";
      (bySource[key] ??= []).push({ mediaStart: s.media_start, duration: s.duration });
    }
    if (Object.keys(bySource).length === 0) continue;

    const edits: TimelineCmd[] = [];
    const matchedClipNames = new Set<string>();
    for (const [clipName, silences] of Object.entries(bySource)) {
      const nameNoExt = clipName.replace(/\.[^.]+$/, "");
      const matching = tlClips.filter(
        (c) => c.name === clipName || c.clipId === clipName || c.name === nameNoExt || c.clipId === nameNoExt,
      );
      for (const tl of matching) {
        matchedClipNames.add(tl.name ?? tl.clipId ?? clipName);
        const srcFrom = tl.mediaStart;
        const srcTo = tl.mediaStart + tl.duration;
        const tlOffset = tl.start - tl.mediaStart;
        for (const sil of silences) {
          const silStart = sil.mediaStart;
          const silEnd = sil.mediaStart + sil.duration;
          const clampedFrom = Math.max(silStart, srcFrom);
          const clampedTo = Math.min(silEnd, srcTo);
          if (clampedTo - clampedFrom < 0.05) continue;
          edits.push({
            type: "deleteRange",
            from: clampedFrom + tlOffset,
            to: clampedTo + tlOffset,
            ripple: true,
            tlIds: [tl.tlId],
          });
        }
      }
    }
    if (edits.length === 0) continue;

    const totalSec = edits.reduce((a, e) => a + (e.type === "deleteRange" ? e.to - e.from : 0), 0);
    const titleByTool: Record<string, string> = {
      remove_silences: "Stille entfernen",
      find_hesitations: "Zögerungen entfernen",
    };
    const clipList = [...matchedClipNames].join(", ") || "?";
    proposals.push({
      title: titleByTool[toolName] ?? `${toolName} — Vorschlag`,
      summary: `${edits.length} Bereich${edits.length > 1 ? "e" : ""} · ${totalSec.toFixed(1)}s zu entfernen · Ziel: ${clipList}`,
      edits,
      provenance: {
        tool: toolName,
        params: (actionEvt?.args ?? {}) as Record<string, unknown>,
        agentThought,
      },
    });
  }
  return proposals;
}

/**
 * Parse un chunk SSE (peut contenir plusieurs events séparés par `\n\n`).
 * Retourne les events complets + un buffer résiduel (event tronqué à la fin).
 */
export function parseSseChunk(
  buffer: string,
  onEvent: (data: string) => void,
): string {
  let workBuffer = buffer;
  let idx: number;
  while ((idx = workBuffer.indexOf("\n\n")) !== -1) {
    const rawEvent = workBuffer.slice(0, idx);
    workBuffer = workBuffer.slice(idx + 2);
    // Un event peut avoir plusieurs lignes (event:, data:, id:, retry:).
    // On extrait toutes les `data:` et les concatène (spec SSE).
    const dataLines = rawEvent
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart());
    if (dataLines.length > 0) onEvent(dataLines.join("\n"));
  }
  return workBuffer;
}
