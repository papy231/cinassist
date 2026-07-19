"use client";
/**
 * ProposalGhostLayers.tsx — layers visuels HITL pour les proposals pending.
 *
 * Rendu de fantômes jaunes pointillés au-dessus de la timeline pour montrer
 * ce que l'agent (ou un pipeline déterministe) propose de faire. Les fantômes
 * sont non-interactifs (pointerEvents: "none") — l'accept/reject se fait via
 * un panel séparé.
 *
 * Cette itération : `split` (ligne verticale) + `delete` (rect hachuré).
 * Autres cmds (setFade, setGain, insert…) viendront ensuite.
 */

import { useProposalStore } from "@/lib/proposals";
import type { TimelineCmd } from "@/lib/timeline-commands";

type ClipShape = {
  tlId: string;
  start: number;
  duration: number;
  videoTrackIndex?: number;
  audioTrackIndex?: number;
  hasAudio?: boolean;
  avLinked?: boolean;
  audioStart?: number;
  // Champs additionnels tolérés (le composant n'en dépend pas mais l'appelant
  // passe un TLClip complet).
  [extra: string]: unknown;
};

const GHOST_COLOR = "#e5c100";

/**
 * Lignes verticales pour tous les `split` pending + bandes hachurées pour les
 * `deleteRange`. Traverse toutes les rows (top:0 bottom:0) — à monter dans le
 * container timeline.
 */
export function ProposalSplitsLayer({ totalDuration }: { totalDuration: number }) {
  const proposals = useProposalStore((s) => s.proposals);
  const pending = proposals.filter((p) => p.status === "pending");
  const splits: Array<{ id: string; at: number; title: string }> = [];
  const ranges: Array<{ id: string; from: number; to: number; title: string }> = [];
  for (const p of pending) {
    p.edits.forEach((e, i) => {
      if (e.type === "split") splits.push({ id: `${p.id}-s-${i}`, at: e.at, title: p.title });
      else if (e.type === "deleteRange") ranges.push({ id: `${p.id}-r-${i}`, from: e.from, to: e.to, title: p.title });
    });
  }
  if (splits.length === 0 && ranges.length === 0) return null;
  if (totalDuration <= 0) return null;
  return (
    <>
      {ranges.map((r) => (
        <div
          key={r.id}
          title={`Vorschlag: Bereich ${r.from.toFixed(2)}s→${r.to.toFixed(2)}s löschen (${r.title})`}
          style={{
            position: "absolute",
            left: `${(r.from / totalDuration) * 100}%`,
            width: `${Math.max(0, ((r.to - r.from) / totalDuration) * 100)}%`,
            top: 0,
            bottom: 0,
            background: "repeating-linear-gradient(45deg, rgba(229,193,0,0.32) 0 6px, rgba(0,0,0,0.15) 6px 12px)",
            borderLeft: `2px dashed ${GHOST_COLOR}`,
            borderRight: `2px dashed ${GHOST_COLOR}`,
            zIndex: 8,
            pointerEvents: "none",
            boxShadow: "inset 0 0 8px rgba(229,193,0,0.35)",
          }}
        />
      ))}
      {splits.map((s) => (
        <div
          key={s.id}
          title={`Vorschlag: Cut bei ${s.at.toFixed(2)}s (${s.title})`}
          style={{
            position: "absolute",
            left: `${(s.at / totalDuration) * 100}%`,
            top: 0,
            bottom: 0,
            width: 0,
            borderLeft: `2px dashed ${GHOST_COLOR}`,
            zIndex: 9,
            pointerEvents: "none",
            filter: "drop-shadow(0 0 3px rgba(229,193,0,0.7))",
          }}
        />
      ))}
    </>
  );
}

/**
 * Rectangle hachuré pour chaque clip prévu au `delete` dans la row donnée.
 * À monter dans chaque row (V et A) — les positions se calculent depuis
 * `clipToPct` / `clipWidthPct` déjà utilisés dans Editor.tsx.
 */
export function ProposalDeletesInRow({
  tlClips,
  kind,
  trackIndex,
  clipToPct,
  clipWidthPct,
}: {
  tlClips: ClipShape[];
  kind: "v" | "a";
  trackIndex: number;
  clipToPct: (s: number) => number;
  clipWidthPct: (d: number) => number;
}) {
  // Calculé inline pour ne pas dépendre d'une closure externe (couplage plus
  // faible avec Editor.tsx). Mêmes règles que Editor.audioStartOf.
  const audioStartOf = (c: ClipShape): number =>
    c.avLinked === false && c.audioStart != null ? c.audioStart : c.start;
  const proposals = useProposalStore((s) => s.proposals);
  const pending = proposals.filter((p) => p.status === "pending");
  const deleteIds = new Set<string>();
  const titleById: Record<string, string> = {};
  for (const p of pending) {
    for (const e of p.edits) {
      if (e.type === "delete") {
        for (const id of e.tlIds) {
          deleteIds.add(id);
          titleById[id] = p.title;
        }
      }
    }
  }
  if (deleteIds.size === 0) return null;
  const targets = tlClips.filter((c) => {
    if (!deleteIds.has(c.tlId)) return false;
    if (kind === "v") return (c.videoTrackIndex ?? 0) === trackIndex;
    return !!c.hasAudio && (c.audioTrackIndex ?? c.videoTrackIndex ?? 0) === trackIndex;
  });
  if (targets.length === 0) return null;
  return (
    <>
      {targets.map((c) => (
        <div
          key={`gh-del-${c.tlId}`}
          title={`Vorschlag: Clip löschen (${titleById[c.tlId] ?? ""})`}
          style={{
            position: "absolute",
            left: `${clipToPct(kind === "v" ? c.start : audioStartOf(c))}%`,
            width: `${clipWidthPct(c.duration)}%`,
            top: 0,
            bottom: 0,
            background: "repeating-linear-gradient(45deg, rgba(229,193,0,0.42) 0 6px, rgba(0,0,0,0.12) 6px 12px)",
            border: `2px dashed ${GHOST_COLOR}`,
            borderRadius: 6,
            pointerEvents: "none",
            zIndex: 8,
            boxShadow: "0 0 6px rgba(229,193,0,0.55)",
          }}
        />
      ))}
    </>
  );
}

// Discriminated helpers exposés pour tests futurs.
export function getPendingSplits(): Array<{ at: number; title: string }> {
  const pending = useProposalStore.getState().proposals.filter((p) => p.status === "pending");
  const out: Array<{ at: number; title: string }> = [];
  for (const p of pending) {
    for (const e of p.edits) {
      if (e.type === "split") out.push({ at: e.at, title: p.title });
    }
  }
  return out;
}

export function getPendingDeleteIds(): Set<string> {
  const pending = useProposalStore.getState().proposals.filter((p) => p.status === "pending");
  const out = new Set<string>();
  for (const p of pending) {
    for (const e of p.edits) {
      if (e.type === "delete") for (const id of e.tlIds) out.add(id);
    }
  }
  return out;
}

// Silence unused import type warning (kept for future work).
export type _Unused = TimelineCmd;
