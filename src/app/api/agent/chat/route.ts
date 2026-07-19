/**
 * /api/agent/chat — proxy Next.js vers le vrai backend FastAPI (port 8001).
 *
 * Reçoit `{ message: string }` du ChatPanel.
 * Appelle `POST http://localhost:8001/api/agent/run_sync` (Ollama qwen2.5:14b + ReAct).
 * Convertit la trace backend → `{ reply, proposals[] }` compatible frontend.
 *
 * Fallback : si le backend ne répond pas, on retourne un message d'erreur clair
 * (sans crash) pour que le user comprenne qu'il faut démarrer `./start.sh`.
 */

import { NextRequest, NextResponse } from "next/server";
import type { TimelineCmd } from "@/lib/timeline-commands";

const BACKEND_URL = process.env.CINASSIST_BACKEND_URL ?? "http://localhost:8001";
// qwen2.5:14b local + ReAct 12 iterations max → jusqu'à 3-4 min sur Mac mini.
// Timeout large pour éviter les 502 spurious. Le prochain ticket streaming
// (SSE via /api/agent/run) évitera d'attendre en aveugle.
const TIMEOUT_MS = 300_000;

type BackendTraceEvent = {
  type: "thought" | "action" | "observation" | "done";
  step: number;
  name?: string;
  args?: Record<string, unknown>;
  content?: unknown;
};

type BackendResponse = {
  final_answer: string | null;
  trace: BackendTraceEvent[];
  step_count: number;
};

type ProposalPayload = {
  title: string;
  summary?: string;
  edits: TimelineCmd[];
  provenance?: {
    tool: string;
    params?: Record<string, unknown>;
    agentThought?: string;
  };
};

type SnapshotClip = {
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

type SnapshotState = {
  totalDuration: number;
  fps: number;
  numVideoTracks: number;
  numAudioTracks: number;
  clips: SnapshotClip[];
};

/**
 * Convertit la trace backend en proposals frontend. Pour l'instant, on gère
 * uniquement les observations qui contiennent `segments` ou `segments_preview`
 * (produites par `remove_silences`, `find_hesitations`, etc.) → un split au
 * `start` de chaque segment. Les deletes/ripples suivront (nécessitent le
 * matching aux tlIds du frontend, à venir au ticket 6).
 */
/**
 * Convertit la trace backend en proposals frontend.
 *
 * Le tool `remove_silences` renvoie des segments À GARDER (portions parlées),
 * avec `clip_name` + `media_start` + `duration` dans le domaine du CLIP SOURCE
 * (pas de la timeline). Il faut :
 *   (a) grouper par clip_name
 *   (b) trouver les tlClips correspondants dans le snapshot
 *   (c) pour chaque tlClip, calculer les silences = gaps entre segments
 *   (d) traduire les positions source → positions timeline via
 *       `timelineTime = mediaTime + (tlClip.start - tlClip.mediaStart)`
 *   (e) émettre un `deleteRange` par silence
 */
function tracetoProposals(trace: BackendTraceEvent[], timelineState?: SnapshotState): ProposalPayload[] {
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

    // `silences` = intervalles à ENLEVER (source domain), directement fournis
    // par le backend (patch cleanup.py). Format: {clip_name, media_start, duration}.
    const silencesRaw = obs.silences as unknown;
    if (!Array.isArray(silencesRaw) || silencesRaw.length === 0) continue;

    // Groupe par clip_name pour matching avec les tlClips du snapshot.
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
    let matchedTlClips = 0;
    for (const [clipName, silences] of Object.entries(bySource)) {
      // Matching souple : nom exact OU sans extension.
      const nameNoExt = clipName.replace(/\.[^.]+$/, "");
      const matching = tlClips.filter(
        (c) => c.name === clipName || c.clipId === clipName || c.name === nameNoExt || c.clipId === nameNoExt,
      );
      for (const tl of matching) {
        matchedTlClips++;
        matchedClipNames.add(tl.name ?? tl.clipId ?? clipName);
        const srcFrom = tl.mediaStart;
        const srcTo = tl.mediaStart + tl.duration;
        const tlOffset = tl.start - tl.mediaStart;
        for (const sil of silences) {
          const silStart = sil.mediaStart;
          const silEnd = sil.mediaStart + sil.duration;
          // Clip aux bornes source du tlClip (le silence peut déborder).
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

export async function POST(req: NextRequest) {
  let message = "";
  let timelineState: unknown = undefined;
  try {
    const body = await req.json();
    message = String(body?.message ?? "");
    timelineState = body?.timeline_state;
  } catch {
    return NextResponse.json({ reply: "Ungültiger Request." }, { status: 400 });
  }
  if (!message.trim()) {
    return NextResponse.json({ reply: "Leere Anfrage." }, { status: 400 });
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    // Forward le state timeline si présent — le backend actuel ignore le champ,
    // mais on prépare le terrain pour le system prompt contextuel (ticket suivant).
    const payload: Record<string, unknown> = { prompt: message };
    if (timelineState) payload.timeline_state = timelineState;
    const res = await fetch(`${BACKEND_URL}/api/agent/run_sync`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      return NextResponse.json(
        { reply: `Backend-Fehler ${res.status}: ${errText || res.statusText}` },
        { status: 502 },
      );
    }
    const data = (await res.json()) as BackendResponse;
    const reply = data.final_answer ?? "Kein final_answer vom Agent erhalten.";
    const proposals = tracetoProposals(data.trace ?? [], timelineState as SnapshotState | undefined);
    return NextResponse.json({ reply, proposals });
  } catch (err) {
    const msg = (err as Error).message;
    const hint = msg.includes("aborted")
      ? "Timeout — der Agent hat zu lange gebraucht."
      : msg.includes("ECONNREFUSED") || msg.includes("fetch failed")
        ? `Backend nicht erreichbar (${BACKEND_URL}). Läuft \`./start.sh\` ?`
        : msg;
    return NextResponse.json({ reply: `Fehler: ${hint}` }, { status: 502 });
  } finally {
    clearTimeout(timer);
  }
}
