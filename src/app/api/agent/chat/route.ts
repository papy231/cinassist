/**
 * /api/agent/chat — proxy Next.js vers le vrai backend FastAPI (port 8001).
 *
 * Reçoit `{ message: string }` du ChatPanel.
 * Appelle `POST http://localhost:8001/api/agent/run_sync` (Ollama qwen2.5:14b + ReAct).
 * Convertit la trace backend → `{ reply, proposals[] }` compatible frontend.
 *
 * NB : le mode par défaut du ChatPanel est désormais streaming (route
 * `/api/agent/chat/stream`). Ce endpoint sync reste pour fallback / debug /
 * appels programmatiques qui préfèrent une réponse en une fois.
 *
 * Fallback : si le backend ne répond pas, on retourne un message d'erreur clair
 * (sans crash) pour que le user comprenne qu'il faut démarrer `./start.sh`.
 */

import { NextRequest, NextResponse } from "next/server";
import {
  type BackendTraceEvent,
  type SnapshotState,
  traceToProposals,
} from "@/lib/agent-trace";

const BACKEND_URL = process.env.CINASSIST_BACKEND_URL ?? "http://localhost:8001";
const TIMEOUT_MS = 300_000;

type BackendResponse = {
  final_answer: string | null;
  trace: BackendTraceEvent[];
  step_count: number;
};

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
    const proposals = traceToProposals(data.trace ?? [], timelineState as SnapshotState | undefined);
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
