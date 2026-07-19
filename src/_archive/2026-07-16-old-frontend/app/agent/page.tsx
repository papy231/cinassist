"use client";

/**
 * CinAssist — Chat Agent Page (Vague 1.5)
 *
 * Interface conversationnelle pour l'agent ReAct backend.
 * Stream SSE en temps réel : chaque thought / action / observation / done
 * apparaît dans la conversation dès qu'il est produit par le modèle.
 */

import { useEffect, useRef, useState } from "react";
import { Bot, Send, Wrench, Eye, CheckCircle2, Loader2, User } from "lucide-react";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

type EventType = "thought" | "action" | "observation" | "done";

interface AgentEvent {
  type: EventType;
  step: number;
  content?: unknown;
  name?: string;
  args?: Record<string, unknown>;
  meta?: { wall_s: number; tokens: number; tokens_per_s: number };
}

interface Turn {
  id: string;
  role: "user" | "agent";
  prompt?: string;
  events: AgentEvent[];
  running: boolean;
}

export default function AgentPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || busy) return;
    const p = prompt.trim();
    setPrompt("");
    setBusy(true);

    const turnId = crypto.randomUUID();
    setTurns((t) => [
      ...t,
      { id: turnId + "-u", role: "user", prompt: p, events: [], running: false },
      { id: turnId, role: "agent", events: [], running: true },
    ]);

    try {
      const res = await fetch(`${API_URL}/api/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: p }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const chunks = buf.split("\n\n");
        buf = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.trim();
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          try {
            const evt = JSON.parse(raw) as AgentEvent;
            setTurns((t) =>
              t.map((x) =>
                x.id === turnId
                  ? { ...x, events: [...x.events, evt] }
                  : x
              )
            );
          } catch (e) {
            console.warn("Failed to parse SSE chunk", e, raw);
          }
        }
      }
    } catch (err) {
      setTurns((t) =>
        t.map((x) =>
          x.id === turnId
            ? {
                ...x,
                events: [
                  ...x.events,
                  {
                    type: "done",
                    step: -1,
                    content: `❌ Fehler: ${(err as Error).message}`,
                  },
                ],
              }
            : x
        )
      );
    } finally {
      setTurns((t) =>
        t.map((x) => (x.id === turnId ? { ...x, running: false } : x))
      );
      setBusy(false);
    }
  }

  const suggestions = [
    "Wie viele Clips habe ich und wie lang sind sie insgesamt?",
    "Finde die Szenen mit Autos auf der Straße",
    "Beschreibe mir meinen Clip mit dem kochenden Koch",
    "Exportiere die 3 Clips als FCPXML für Premiere",
  ];

  return (
    <div className="flex h-screen flex-col bg-neutral-950 text-neutral-100">
      {/* Header */}
      <header className="border-b border-white/10 px-6 py-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-500 to-purple-500 grid place-items-center">
          <Bot size={20} />
        </div>
        <div>
          <h1 className="font-semibold text-lg">CinAssist-Agent</h1>
          <p className="text-xs text-neutral-400">
            qwen2.5:14b · lokal · 15 Tools
          </p>
        </div>
      </header>

      {/* Chat */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {turns.length === 0 && (
            <div className="text-center pt-12">
              <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-blue-500/20 to-purple-500/20 grid place-items-center mb-4">
                <Bot size={32} className="text-blue-400" />
              </div>
              <h2 className="text-xl font-medium mb-2">
                Sag mir, was du machen willst.
              </h2>
              <p className="text-sm text-neutral-400 mb-8">
                Ich kann deine Rushes durchsuchen, filtern, beschreiben und als FCPXML exportieren.
              </p>
              <div className="grid gap-2 max-w-md mx-auto">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    onClick={() => setPrompt(s)}
                    className="text-left text-sm px-4 py-3 rounded-xl border border-white/10 hover:bg-white/5 transition"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t) => (
            <div key={t.id}>
              {t.role === "user" ? (
                <UserBubble text={t.prompt || ""} />
              ) : (
                <AgentBubble events={t.events} running={t.running} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Input */}
      <form
        onSubmit={submit}
        className="border-t border-white/10 px-6 py-4"
      >
        <div className="mx-auto max-w-3xl flex gap-2">
          <input
            type="text"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={
              busy ? "Der Agent überlegt…" : "Frag den Agenten…"
            }
            disabled={busy}
            className="flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-3 focus:outline-none focus:border-blue-500 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy || !prompt.trim()}
            className="px-5 py-3 rounded-xl bg-blue-500 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed transition flex items-center gap-2"
          >
            {busy ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
          </button>
        </div>
      </form>
    </div>
  );
}

// ─── Bubbles ────────────────────────────────────────────────
function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex gap-3 justify-end">
      <div className="max-w-xl bg-blue-500/20 border border-blue-500/30 rounded-2xl px-4 py-3">
        <p className="text-sm">{text}</p>
      </div>
      <div className="w-8 h-8 rounded-full bg-neutral-800 grid place-items-center shrink-0">
        <User size={16} />
      </div>
    </div>
  );
}

function AgentBubble({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const done = events.find((e) => e.type === "done");
  return (
    <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-500 grid place-items-center shrink-0">
        <Bot size={16} />
      </div>
      <div className="flex-1 space-y-2">
        {events
          .filter((e) => e.type !== "done")
          .map((evt, i) => (
            <EventLine key={i} evt={evt} />
          ))}
        {done && (
          <div className="bg-white/5 border border-white/10 rounded-2xl px-4 py-3">
            <p className="text-sm whitespace-pre-wrap">{String(done.content)}</p>
          </div>
        )}
        {running && !done && (
          <div className="flex items-center gap-2 text-xs text-neutral-500">
            <Loader2 size={12} className="animate-spin" />
            en cours…
          </div>
        )}
      </div>
    </div>
  );
}

function EventLine({ evt }: { evt: AgentEvent }) {
  const wrap = "flex items-start gap-2 text-xs px-3 py-2 rounded-lg";

  if (evt.type === "thought") {
    return (
      <div className={`${wrap} bg-neutral-800/50 text-neutral-400`}>
        <Bot size={12} className="mt-0.5 shrink-0 text-blue-400" />
        <div className="flex-1">
          <span className="italic">{String(evt.content)}</span>
          {evt.meta && (
            <span className="ml-2 text-neutral-600">
              · {evt.meta.wall_s}s · {evt.meta.tokens_per_s}t/s
            </span>
          )}
        </div>
      </div>
    );
  }
  if (evt.type === "action") {
    return (
      <div className={`${wrap} bg-amber-500/10 text-amber-200 border border-amber-500/20`}>
        <Wrench size={12} className="mt-0.5 shrink-0" />
        <div className="flex-1 font-mono">
          <span className="font-semibold">{evt.name}</span>(
          <span className="text-amber-300/70">
            {evt.args ? JSON.stringify(evt.args) : ""}
          </span>
          )
        </div>
      </div>
    );
  }
  if (evt.type === "observation") {
    const s = JSON.stringify(evt.content);
    const preview = s.length > 220 ? s.slice(0, 220) + "…" : s;
    return (
      <div className={`${wrap} bg-emerald-500/5 text-emerald-200/80 border border-emerald-500/20`}>
        <Eye size={12} className="mt-0.5 shrink-0" />
        <div className="flex-1 font-mono text-emerald-200/60">{preview}</div>
      </div>
    );
  }
  return null;
}
