/**
 * /api/agent/chat/stream — proxy SSE Next.js → FastAPI /api/agent/run.
 *
 * Le backend émet un flux `text/event-stream` avec des events ReAct
 * (thought / action / observation / done). On forwarde le stream tel quel
 * au frontend (pas de buffering), et on laisse ChatPanel parser + convertir
 * la trace en proposals à la fin.
 *
 * Timeout large côté fetch mais pas de setTimeout : on veut que le stream
 * puisse tourner aussi longtemps que le backend le veut (jusqu'au "done").
 */

import { NextRequest } from "next/server";

const BACKEND_URL = process.env.CINASSIST_BACKEND_URL ?? "http://localhost:8001";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Small-talk short-circuit : détecte les salutations et messages triviaux pour
 * éviter de payer 30-40s d'agent ReAct pour dire "bonjour". Retourne une
 * réponse préparée si match, null sinon. Rythme de démo digne.
 */
function smallTalkResponse(msg: string): string | null {
  const m = msg.trim().toLowerCase().replace(/[!?.,;]+$/g, "").trim();
  const words = m.split(/\s+/);
  if (words.length > 6) return null;

  const greetings = [
    "hallo", "hi", "hey", "moin", "servus", "guten tag", "guten morgen", "guten abend",
    "salut", "bonjour", "coucou", "hello", "hola", "yo", "hallöchen", "hej",
  ];
  if (greetings.some((g) => m === g || m.startsWith(g + " "))) {
    return "Hallo! Ich bin dein KI-Schnittassistent. Sag mir was du machen willst — z. B. « Stille entfernen », « Beste Takes finden », « Zeige alle Sprecher », oder ziehe direkt Clips auf die Timeline.";
  }

  const thanks = ["danke", "merci", "thanks", "thx", "vielen dank", "super", "cool", "top", "perfekt", "ok"];
  if (thanks.some((t) => m === t)) {
    return "Gerne — sag Bescheid wenn du weitere Hilfe brauchst.";
  }

  const help = ["hilfe", "help", "aide", "was kannst du", "was machst du", "?"];
  if (help.some((h) => m === h || m === h + "?")) {
    return "Ich kann u. a. :\n• Stille & Zögerungen entfernen\n• Beste Takes finden (Framing, Sprecher, Transkription)\n• Rohschnitt aus deinen Clips bauen\n• Multicam synchronisieren\n• Nach Szenen suchen (« zeige mir alle Straßenaufnahmen »)\n• An DaVinci/Premiere/FCP/AVID senden\n\nStell einfach eine Frage in natürlicher Sprache.";
  }
  return null;
}

function sseEvent(obj: Record<string, unknown>): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

export async function POST(req: NextRequest) {
  let message = "";
  let timelineState: unknown = undefined;
  try {
    const body = await req.json();
    message = String(body?.message ?? "");
    timelineState = body?.timeline_state;
  } catch {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ message: "Ungültiger Request." })}\n\n`,
      { status: 400, headers: { "Content-Type": "text/event-stream" } },
    );
  }
  if (!message.trim()) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ message: "Leere Anfrage." })}\n\n`,
      { status: 400, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  // Short-circuit small-talk : skip complètement le backend agent (qwen2.5:14b
  // + system prompt de 92 lignes + 17 tools = 30-40s même pour "hallo").
  const canned = smallTalkResponse(message);
  if (canned) {
    return new Response(
      sseEvent({ type: "done", step: 0, content: canned }),
      {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
          "X-Accel-Buffering": "no",
        },
      },
    );
  }

  const payload: Record<string, unknown> = { prompt: message };
  if (timelineState) payload.timeline_state = timelineState;

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/api/agent/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(payload),
      // Pas de signal/timeout — le backend peut tourner plusieurs minutes.
    });
  } catch (err) {
    const msg = (err as Error).message;
    const hint =
      msg.includes("ECONNREFUSED") || msg.includes("fetch failed")
        ? `Backend nicht erreichbar (${BACKEND_URL}). Läuft ./start.sh ?`
        : msg;
    return new Response(
      `event: error\ndata: ${JSON.stringify({ message: `Fehler: ${hint}` })}\n\n`,
      { status: 502, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  if (!upstream.ok || !upstream.body) {
    const errText = await upstream.text().catch(() => "");
    return new Response(
      `event: error\ndata: ${JSON.stringify({ message: `Backend-Fehler ${upstream.status}: ${errText || upstream.statusText}` })}\n\n`,
      { status: 502, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  // Pipe explicite via TransformStream — Next 16 dev bufferise parfois
  // `new Response(upstream.body)` malgré Content-Type text/event-stream. Le pipe
  // manuel force le passthrough chunk-par-chunk sans attendre le end.
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const reader = upstream.body.getReader();
  const writer = writable.getWriter();
  (async () => {
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value) await writer.write(value);
      }
    } catch {
      /* upstream cassé — on ferme quand même */
    } finally {
      try { await writer.close(); } catch { /* déjà fermé */ }
    }
  })();

  return new Response(readable, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Anti-buffer sur les reverse-proxies (nginx, Cloudflare)
      "X-Accel-Buffering": "no",
    },
  });
}
