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
            animation: "cinProposalPulse 2.4s ease-in-out infinite",
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
            animation: "cinProposalPulse 2.4s ease-in-out infinite",
          }}
        />
      ))}
    </>
  );
}

/**
 * Badge résumé flottant au-dessus du 1er ghost de chaque proposal pending —
 * `47 Vorschläge · 12.3s` avec accept/reject à portée de clic. Placé dans le
 * container timeline (position absolute), visible même si le user scrolle
 * hors du chat panel.
 */
export function ProposalSummaryBadges({ totalDuration }: { totalDuration: number }) {
  const proposals = useProposalStore((s) => s.proposals);
  const acceptProposal = useProposalStore((s) => s.acceptProposal);
  const rejectProposal = useProposalStore((s) => s.rejectProposal);
  const pending = proposals.filter((p) => p.status === "pending");
  if (pending.length === 0 || totalDuration <= 0) return null;

  return (
    <>
      {pending.map((p) => {
        // Trouve le 1er edit à position temporelle pour placer le badge.
        let firstT: number | null = null;
        let totalSec = 0;
        let count = 0;
        for (const e of p.edits) {
          if (e.type === "deleteRange") {
            if (firstT == null || e.from < firstT) firstT = e.from;
            totalSec += Math.max(0, e.to - e.from);
            count++;
          } else if (e.type === "split") {
            if (firstT == null || e.at < firstT) firstT = e.at;
            count++;
          } else if (e.type === "delete") {
            count += e.tlIds.length;
          }
        }
        if (firstT == null) firstT = 0;
        const leftPct = Math.min(98, (firstT / totalDuration) * 100);
        return (
          <div
            key={`badge-${p.id}`}
            style={{
              position: "absolute",
              left: `${leftPct}%`,
              top: 24, // sous la ruler, au-dessus des rows
              transform: "translateX(-4px)",
              zIndex: 12,
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "3px 8px",
              borderRadius: 6,
              background: "rgba(24,24,26,0.94)",
              border: `1px solid ${GHOST_COLOR}`,
              color: GHOST_COLOR,
              fontSize: 10,
              fontWeight: 700,
              fontFamily: "ui-monospace, monospace",
              boxShadow: "0 3px 12px rgba(0,0,0,0.6), 0 0 12px rgba(229,193,0,0.35)",
              animation: "cinBadgeIn 0.25s ease-out",
              whiteSpace: "nowrap",
            }}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke={GHOST_COLOR} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2L2 22h20L12 2zM12 9v4M12 17h.01" /></svg>
            <span>{count} {count > 1 ? "Vorschläge" : "Vorschlag"}{totalSec > 0 ? ` · ${totalSec.toFixed(1)}s` : ""}</span>
            <button
              onClick={(e) => { e.stopPropagation(); acceptProposal(p.id); }}
              title="Annehmen (Cmd-Enter)"
              style={{ background: GHOST_COLOR, color: "#1a1a1c", border: "none", borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", pointerEvents: "auto" }}
            >
              OK
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); rejectProposal(p.id); }}
              title="Ablehnen"
              style={{ background: "transparent", color: "#8a8a8a", border: "1px solid rgba(255,255,255,0.15)", borderRadius: 4, padding: "1px 5px", fontSize: 10, cursor: "pointer", fontFamily: "inherit", pointerEvents: "auto", display: "flex", alignItems: "center" }}
            >
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
            </button>
          </div>
        );
      })}
    </>
  );
}

// Retourne la position temporelle du 1er edit d'une proposal pending — utile
// pour l'auto-scroll depuis Editor.
export function getFirstPendingProposalTime(): { t: number; id: string } | null {
  const pending = useProposalStore.getState().proposals.filter((p) => p.status === "pending");
  for (const p of pending) {
    for (const e of p.edits) {
      if (e.type === "deleteRange") return { t: e.from, id: p.id };
      if (e.type === "split") return { t: e.at, id: p.id };
    }
  }
  return null;
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
