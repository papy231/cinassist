"use client";
/**
 * ChatPanel.tsx — Panel flottant du KI-Schnittassistent.
 *
 * Bouton FAB en bas-droite ; s'ouvre en side panel 400×500. Message list +
 * input textarea. Envoi POST vers `/api/agent/chat`. La réponse peut inclure
 * des `proposals` qui sont poussées automatiquement dans le ProposalStore
 * (→ ghost overlay visible sur la timeline).
 *
 * Pour l'instant : backend mock (Next.js API route). Ticket 5 branchera au
 * vrai FastAPI + Ollama.
 */

import { useEffect, useRef, useState } from "react";
import { useChatStore, type ChatStep } from "@/lib/chat-store";
import { useProposalStore } from "@/lib/proposals";
import { parseSseChunk, traceToProposals, type BackendTraceEvent, type SnapshotState } from "@/lib/agent-trace";
import { useStylePrefsStore, type Language, type CuttingStyle } from "@/lib/style-prefs";
import { useIsMobile } from "@/lib/use-media-query";

const ACCENT = "#e5c100";
const BG_PANEL = "#1c1c1e";
const BG_INPUT = "#242426";
const BG_USER = "#2f4a70";
const BG_ASSISTANT = "#232326";

/**
 * Cards de suggestions proactives (agent proactif après ingest d'un clip).
 * Clic sur une card → envoie le prompt à l'agent normal.
 */
function ProactiveSuggestions({
  suggestions,
  onPick,
}: {
  suggestions: import("@/lib/chat-store").ChatProactiveSuggestion[];
  onPick: (prompt: string) => void;
}) {
  const iconFor = (key?: string) => {
    const c = "currentColor";
    const sw = 1.9;
    switch (key) {
      case "users":
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>;
      case "scissors":
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4L8.5 15.5M20 20L8.5 8.5" /></svg>;
      case "volume-off":
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5zM22 9l-6 6M16 9l6 6" /></svg>;
      case "film":
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.18" /><path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 17h5M17 7h5" /></svg>;
      case "star":
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" /></svg>;
      default:
        return <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" /></svg>;
    }
  };
  return (
    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 5 }}>
      {suggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => onPick(s.prompt)}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            padding: "7px 10px",
            borderRadius: 7,
            background: "rgba(229,193,0,0.08)",
            border: "1px solid rgba(229,193,0,0.35)",
            color: "#e5c100",
            fontSize: 11.5,
            fontFamily: "inherit",
            textAlign: "left",
            cursor: "pointer",
            transition: "background 0.12s, border-color 0.12s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(229,193,0,0.16)"; e.currentTarget.style.borderColor = "rgba(229,193,0,0.6)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(229,193,0,0.08)"; e.currentTarget.style.borderColor = "rgba(229,193,0,0.35)"; }}
        >
          <span style={{ flex: "none", opacity: 0.9, marginTop: 1 }}>{iconFor(s.icon)}</span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700 }}>{s.title}</div>
            <div style={{ fontSize: 10.5, color: "#c9b464", marginTop: 2, fontWeight: 400 }}>{s.description}</div>
          </span>
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" style={{ flex: "none", opacity: 0.55, marginTop: 3 }}><path d="M9 6l6 6-6 6" /></svg>
        </button>
      ))}
    </div>
  );
}

/**
 * Badge fin en bas de la bulle assistant : temps total · tokens · étapes ReAct.
 * Sobre pour ne pas noyer la réponse, mais visible pour la thèse Bachelorarbeit
 * (traces d'observabilité LLM).
 */
function LatencyBadge({ stats }: { stats: import("@/lib/chat-store").ChatLatencyStats }) {
  const fmt = (s: number) => (s >= 60 ? `${(s / 60).toFixed(1)}min` : `${s.toFixed(1)}s`);
  const parts: string[] = [];
  parts.push(fmt(stats.elapsedSec));
  if (stats.totalTokens > 0) parts.push(`${stats.totalTokens} tok`);
  if (stats.toolCallCount > 0) parts.push(`${stats.toolCallCount} tool${stats.toolCallCount > 1 ? "s" : ""}`);
  if (stats.stepCount > 0) parts.push(`${stats.stepCount} step${stats.stepCount > 1 ? "s" : ""}`);
  const tokPerS = stats.totalWallSec > 0 && stats.totalTokens > 0 ? stats.totalTokens / stats.totalWallSec : null;
  const title = [
    `Gesamte Zeit: ${fmt(stats.elapsedSec)}`,
    stats.totalWallSec > 0 ? `LLM-Wall: ${fmt(stats.totalWallSec)}` : null,
    stats.totalTokens > 0 ? `Tokens: ${stats.totalTokens}` : null,
    tokPerS ? `Durchsatz: ${tokPerS.toFixed(1)} tok/s` : null,
    stats.toolCallCount > 0 ? `Tool-Calls: ${stats.toolCallCount}` : null,
    stats.stepCount > 0 ? `ReAct-Schritte: ${stats.stepCount}` : null,
  ].filter(Boolean).join("\n");
  return (
    <div
      title={title}
      style={{
        marginTop: 6,
        paddingTop: 5,
        borderTop: "1px solid rgba(255,255,255,0.06)",
        display: "flex",
        alignItems: "center",
        gap: 5,
        fontSize: 9.5,
        fontFamily: "ui-monospace, monospace",
        color: "#6a6a6a",
      }}
    >
      <svg width={9} height={9} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.7 }}>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4l2.5 2.5" />
      </svg>
      {parts.map((p, i) => (
        <span key={i}>{i > 0 ? "· " : ""}{p}</span>
      ))}
    </div>
  );
}

/**
 * Indicateur affiché entre l'envoi et l'arrivée du 1er event SSE. Ollama
 * (qwen2.5:14b) peut mettre 15-30s avant de produire son premier token — sans
 * ce visuel, l'utilisateur croit que ça bug.
 */
function PreStreamIndicator({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 250);
    return () => clearInterval(iv);
  }, [startedAt]);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "#8a8a8a" }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#e5c100" strokeWidth="2.4" strokeLinecap="round" strokeDasharray="14 42" style={{ animation: "spin 0.9s linear infinite" }}>
        <circle cx="12" cy="12" r="9" />
      </svg>
      <span>Agent denkt nach… <span style={{ fontFamily: "ui-monospace, monospace", color: "#7a7a7a" }}>{elapsed}s</span></span>
    </div>
  );
}

/**
 * Rendu compact des events ReAct pendant/après le stream SSE.
 * - Pendant streaming : tous les steps visibles empilés (compact).
 * - Après done : collapsable via un summary "N Schritte".
 */
function StreamingSteps({ steps, collapsed }: { steps: ChatStep[]; collapsed: boolean }) {
  const [expanded, setExpanded] = useState(!collapsed);
  useEffect(() => { setExpanded(!collapsed); }, [collapsed]);

  const short = (v: unknown, max = 140): string => {
    if (v == null) return "";
    if (typeof v === "string") return v.length > max ? v.slice(0, max) + "…" : v;
    try {
      const s = JSON.stringify(v);
      return s.length > max ? s.slice(0, max) + "…" : s;
    } catch { return String(v); }
  };

  const ChevronRight = () => (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: "middle", marginRight: 4 }}><path d="M9 6l6 6-6 6" /></svg>
  );
  const ChevronDown = () => (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: "middle", marginRight: 4 }}><path d="M6 9l6 6 6-6" /></svg>
  );

  if (collapsed && !expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        style={{ display: "inline-flex", alignItems: "center", marginBottom: 6, background: "transparent", border: "1px solid rgba(255,255,255,0.10)", borderRadius: 5, padding: "2px 7px", fontSize: 10, color: "#8a8a8a", cursor: "pointer", fontFamily: "inherit" }}
      >
        <ChevronRight />{steps.filter((s) => s.type !== "done").length} Schritte anzeigen
      </button>
    );
  }
  return (
    <div style={{ marginBottom: 8, display: "flex", flexDirection: "column", gap: 3, fontSize: 11, lineHeight: 1.35, borderLeft: "2px solid rgba(229,193,0,0.30)", paddingLeft: 8 }}>
      {steps.map((s, i) => {
        if (s.type === "done") return null;
        const color = s.type === "thought" ? "#8a8a8a" : s.type === "action" ? "#b9d94a" : "#7fd4c4";
        // Icônes SVG 12px, stroke = couleur du step, alignées à la baseline du texte.
        const iconSvg = s.type === "thought"
          ? (
            // Bulle de pensée
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M8 12a4 4 0 0 1 4-4 4 4 0 0 1 4 4c0 2-1.5 3-3 3.5V17M12 20h.01" /></svg>
          )
          : s.type === "action"
          ? (
            // Éclair / exécution
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" /></svg>
          )
          : (
            // Flèche retour / résultat
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          );
        let body: string;
        if (s.type === "thought") body = short(s.content, 200);
        else if (s.type === "action") body = `${s.name ?? "?"}(${short(s.args, 90)})`;
        else body = short(s.content, 160);
        return (
          <div key={i} style={{ color, display: "flex", gap: 6, alignItems: "flex-start" }}>
            <span style={{ flex: "none", opacity: 0.75, marginTop: 2 }}>{iconSvg}</span>
            <span style={{ flex: 1, wordBreak: "break-word", fontStyle: s.type === "thought" ? "italic" : "normal", fontFamily: s.type === "action" ? "ui-monospace, monospace" : "inherit", fontSize: s.type === "action" ? 10 : 11 }}>{body}</span>
          </div>
        );
      })}
      {collapsed && (
        <button
          onClick={() => setExpanded(false)}
          style={{ alignSelf: "flex-start", marginTop: 4, display: "inline-flex", alignItems: "center", background: "transparent", border: "none", fontSize: 10, color: "#7a7a7a", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
        >
          <ChevronDown />ausblenden
        </button>
      )}
    </div>
  );
}

function ProposalActions({ proposalId }: { proposalId: string }) {
  const proposal = useProposalStore((s) => s.proposals.find((p) => p.id === proposalId));
  const acceptProposal = useProposalStore((s) => s.acceptProposal);
  const rejectProposal = useProposalStore((s) => s.rejectProposal);
  if (!proposal) return null;

  const nEdits = proposal.edits.length;
  const nLabel = `${nEdits} Aktion${nEdits > 1 ? "en" : ""}`;

  if (proposal.status === "accepted") {
    return (
      <div style={{ marginTop: 10, padding: "6px 10px", background: "rgba(120,200,120,0.14)", border: "1px solid rgba(120,200,120,0.3)", borderRadius: 6, fontSize: 11, color: "#96d996", display: "flex", alignItems: "center", gap: 6 }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#96d996" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
        Angenommen · {nLabel}
      </div>
    );
  }
  if (proposal.status === "rejected") {
    return (
      <div style={{ marginTop: 10, padding: "6px 10px", background: "rgba(200,120,120,0.14)", border: "1px solid rgba(200,120,120,0.3)", borderRadius: 6, fontSize: 11, color: "#d99696", display: "flex", alignItems: "center", gap: 6 }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#d99696" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
        Abgelehnt
      </div>
    );
  }
  // pending or partial
  return (
    <div style={{ marginTop: 10, borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
      {proposal.summary && (
        <div style={{ fontSize: 11, color: "#a0a0a0", fontStyle: "italic" }}>{proposal.summary}</div>
      )}
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <button
          onClick={() => acceptProposal(proposalId)}
          style={{ flex: 1, background: ACCENT, color: "#000", border: "none", borderRadius: 6, padding: "6px 10px", fontWeight: 600, fontSize: 12, cursor: "pointer" }}
        >
          Annehmen
        </button>
        <button
          onClick={() => rejectProposal(proposalId)}
          style={{ flex: 1, background: "transparent", color: "#c8c8c8", border: "1px solid rgba(255,255,255,0.18)", borderRadius: 6, padding: "6px 10px", fontSize: 12, cursor: "pointer" }}
        >
          Ablehnen
        </button>
        <span style={{ fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace", padding: "0 4px" }}>{nLabel}</span>
      </div>
    </div>
  );
}

export default function ChatPanel() {
  const [input, setInput] = useState("");
  const [stylePrefsOpen, setStylePrefsOpen] = useState(false);
  const stylePrefsChanged = useStylePrefsStore((s) => s.changedCount());
  const isMobile = useIsMobile();
  const [openSubmenu, setOpenSubmenu] = useState<{ label: string; top: number; left: number } | null>(null);
  const messages = useChatStore((s) => s.messages);
  const isPending = useChatStore((s) => s.isPending);
  const open = useChatStore((s) => s.isOpen);
  const setOpen = useChatStore((s) => s.setOpen);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, isPending]);

  const sendPrompt = async (msg: string) => {
    msg = msg.trim();
    if (!msg) return;
    const chat = useChatStore.getState();
    chat.addMessage({ role: "user", content: msg });
    chat.setPending(true);

    // Bulle assistant "streaming" — vide au départ, mise à jour à chaque event SSE.
    const assistantMsg = chat.addMessage({
      role: "assistant",
      content: "",
      isStreaming: true,
      steps: [],
    });

    const executor = useProposalStore.getState().executor;
    const rawSnapshot = executor?.getSnapshot();
    // Merge les style prefs utilisateur — le backend lit
    // `timeline_state.style_prefs` et étend son system prompt en conséquence.
    const stylePrefs = useStylePrefsStore.getState().prefs;
    const timeline_state = rawSnapshot ? { ...rawSnapshot, style_prefs: stylePrefs } : { style_prefs: stylePrefs };
    const accumulatedSteps: BackendTraceEvent[] = [];
    let finalAnswer = "";
    let streamErrored = false;

    try {
      const res = await fetch("/api/agent/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message: msg, timeline_state }),
      });
      if (!res.ok || !res.body) {
        const errText = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status} ${errText || res.statusText || ""}`.trim());
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = parseSseChunk(buffer, (dataStr) => {
          let evt: BackendTraceEvent;
          try { evt = JSON.parse(dataStr) as BackendTraceEvent; } catch { return; }
          accumulatedSteps.push(evt);
          if (evt.type === "done") {
            finalAnswer = typeof evt.content === "string" ? evt.content : "";
            return;
          }
          // Push le step dans la bulle assistant courante (rendu live).
          useChatStore.getState().appendStep(assistantMsg.id, evt as ChatStep);
        });
      }
    } catch (err) {
      streamErrored = true;
      const errMsg = (err as Error).message;
      const hint =
        errMsg.includes("ECONNREFUSED") || errMsg.includes("fetch failed") || errMsg.includes("Backend nicht erreichbar")
          ? "Backend nicht erreichbar. Läuft ./start.sh ?"
          : errMsg;
      useChatStore.getState().updateMessage(assistantMsg.id, {
        isStreaming: false,
        content: `Fehler beim Agent-Aufruf: ${hint}`,
      });
    }

    if (streamErrored) {
      chat.setPending(false);
      return;
    }

    // Fin de stream : agrège les stats latence/tokens depuis les events.
    let totalWallSec = 0;
    let totalTokens = 0;
    let toolCallCount = 0;
    for (const ev of accumulatedSteps) {
      if (ev.meta?.wall_s) totalWallSec += ev.meta.wall_s;
      if (ev.meta?.tokens) totalTokens += ev.meta.tokens;
      if (ev.type === "action") toolCallCount++;
    }
    const stepCount = accumulatedSteps.filter((e) => e.type !== "done").length;
    const elapsedSec = (Date.now() - assistantMsg.createdAt) / 1000;

    // Reconstruit les proposals depuis la trace complète.
    let firstProposalId: string | undefined;
    const proposals = traceToProposals(accumulatedSteps, timeline_state as SnapshotState | undefined);
    if (proposals.length > 0) {
      const propStore = useProposalStore.getState();
      for (const p of proposals) {
        const created = propStore.addProposal({
          title: p.title,
          summary: p.summary,
          edits: p.edits,
          createdBy: "agent",
          provenance: p.provenance,
        });
        if (!firstProposalId) firstProposalId = created.id;
      }
    }
    useChatStore.getState().updateMessage(assistantMsg.id, {
      isStreaming: false,
      content: finalAnswer || "(Kein final_answer vom Agent.)",
      proposalId: firstProposalId,
      latency: { totalWallSec, totalTokens, stepCount, toolCallCount, elapsedSec },
    });
    chat.setPending(false);
  };

  const send = async () => {
    const msg = input.trim();
    if (!msg) return;
    setInput("");
    await sendPrompt(msg);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const stilleIcon = <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5L6 9H2v6h4l5 4V5z" /><path d="M23 9l-6 6M17 9l6 6" /></svg>;
  type QuickAction = {
    icon: React.ReactNode;
    label: string;
    prompt?: string;
    accent?: string;
    submenu?: Array<{ label: string; description: string; prompt: string; accent?: string }>;
  };
  const quickActions: QuickAction[] = [
    {
      icon: stilleIcon,
      label: "Stille entfernen",
      submenu: [
        {
          label: "sanft",
          description: "≥ 1.5 s · großer Puffer",
          accent: "#8ed08e",
          prompt:
            "Entferne alle Stille aus dem aktuellen Clip, SANFT. " +
            "Nutze das Tool remove_silences mit args={min_silence_ms: 1500, keep_margin_ms: 250}.",
        },
        {
          label: "normal",
          description: "≥ 0.8 s · Standard",
          prompt:
            "Entferne alle Stille aus dem aktuellen Clip, NORMAL. " +
            "Nutze das Tool remove_silences mit args={min_silence_ms: 800, keep_margin_ms: 150}.",
        },
        {
          label: "aggressiv",
          description: "≥ 0.3 s · knapper Puffer",
          accent: "#ff8f6b",
          prompt:
            "Entferne alle Stille aus dem aktuellen Clip, AGGRESSIV. " +
            "Nutze das Tool remove_silences mit args={min_silence_ms: 300, keep_margin_ms: 80}.",
        },
      ],
    },
    { icon: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>, label: "Zögerungen", prompt: "Entferne alle Zögerungen (ähm, äh, hm) aus dem aktuellen Clip." },
    { icon: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/></svg>, label: "Beste Takes", prompt: "Finde die besten Takes und markiere sie." },
    { icon: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/></svg>, label: "Sprecher", prompt: "Liste alle Sprecher auf." },
    { icon: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.12 15.88M14.47 14.48L20 20M8.12 8.12L12 12"/></svg>, label: "Rohschnitt", prompt: "Erzeuge einen kinematischen Rohschnitt aus dem Material." },
    { icon: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="7" width="20" height="15" rx="2"/><path d="M17 2l-5 5-5-5"/></svg>, label: "Multicam", prompt: "Synchronisiere alle Multicam-Aufnahmen." },
  ];

  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: isMobile ? 0 : 20,
        right: isMobile ? 0 : 20,
        left: isMobile ? 0 : undefined,
        top: isMobile ? 0 : undefined,
        width: isMobile ? "100vw" : 400,
        height: isMobile ? "100vh" : 520,
        background: BG_PANEL,
        border: isMobile ? "none" : "1px solid rgba(255,255,255,0.1)",
        borderRadius: isMobile ? 0 : 12,
        boxShadow: isMobile ? "none" : "0 12px 40px rgba(0,0,0,0.55)",
        display: "flex",
        flexDirection: "column",
        zIndex: 1000,
        overflow: "hidden",
        color: "#e6e6e6",
        fontSize: 13,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
          background: "rgba(229,193,0,0.06)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: ACCENT, boxShadow: `0 0 6px ${ACCENT}` }} />
          <strong style={{ color: ACCENT, fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase" }}>KI-Schnittassistent</strong>
        </div>
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <button
            onClick={() => setStylePrefsOpen(true)}
            title={`Stil-Präferenzen${stylePrefsChanged > 0 ? ` (${stylePrefsChanged} angepasst)` : ""}`}
            style={{ position: "relative", background: "transparent", border: "none", color: stylePrefsChanged > 0 ? ACCENT : "#888", cursor: "pointer", padding: "3px 6px", display: "flex", alignItems: "center" }}
          >
            <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2l-.3-2.5H10.7l-.3 2.5a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.3 2.5h2.6l.3-2.5a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" />
            </svg>
            {stylePrefsChanged > 0 && (
              <span style={{ position: "absolute", top: 1, right: 1, width: 6, height: 6, borderRadius: "50%", background: ACCENT }} />
            )}
          </button>
          <button
            onClick={() => useChatStore.getState().clear()}
            title="Verlauf löschen"
            style={{ background: "transparent", border: "none", color: "#888", cursor: "pointer", fontSize: 11, padding: "2px 6px" }}
          >
            leeren
          </button>
          <button
            onClick={() => setOpen(false)}
            title="Schließen"
            style={{ background: "transparent", border: "none", color: "#aaa", cursor: "pointer", padding: "0 6px", display: "flex", alignItems: "center" }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
      </div>
      {/* Quick actions — chips cliquables qui envoient un prompt prêt à l'emploi. */}
      <div
        style={{
          display: "flex",
          gap: 6,
          padding: "8px 12px",
          overflowX: "auto",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          flexShrink: 0,
          scrollbarWidth: "thin",
        }}
      >
        {quickActions.map((a) => {
          const isOpen = openSubmenu?.label === a.label;
          return (
            <button
              key={a.label}
              onClick={(e) => {
                if (a.submenu) {
                  if (isOpen) {
                    setOpenSubmenu(null);
                  } else {
                    const rect = e.currentTarget.getBoundingClientRect();
                    setOpenSubmenu({ label: a.label, top: rect.bottom + 6, left: rect.left });
                  }
                } else if (a.prompt) {
                  void sendPrompt(a.prompt);
                }
              }}
              disabled={isPending}
              title={a.prompt ?? `${a.label} — Untermenü`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "5px 10px",
                borderRadius: 14,
                background: isOpen ? "#2c2c30" : isPending ? "#1e1e20" : "#232326",
                border: `1px solid ${isOpen ? "rgba(229,193,0,0.4)" : "rgba(255,255,255,0.08)"}`,
                color: isPending ? "#5a5a5a" : "#d0d0d0",
                fontSize: 11,
                whiteSpace: "nowrap",
                cursor: isPending ? "not-allowed" : "pointer",
                fontFamily: "inherit",
                flexShrink: 0,
              }}
            >
              <span style={{ color: isPending ? "#5a5a5a" : (a.accent ?? ACCENT), display: "inline-flex" }}>{a.icon}</span>
              {a.label}
              {a.submenu && (
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6, marginLeft: 2, transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}>
                  <path d="M6 9l6 6 6-6" />
                </svg>
              )}
            </button>
          );
        })}
      </div>
      {/* Submenu du chip actif — rendu en position:fixed pour sortir du container
          overflow-x de la barre chips. Position calculée au moment du clic depuis
          le rect du bouton. Backdrop invisible ferme au clic hors zone. */}
      {openSubmenu && (() => {
        const action = quickActions.find((a) => a.label === openSubmenu.label);
        if (!action?.submenu) return null;
        return (
          <>
            <div onClick={() => setOpenSubmenu(null)} style={{ position: "fixed", inset: 0, zIndex: 1900 }} />
            <div
              style={{
                position: "fixed",
                top: openSubmenu.top,
                left: openSubmenu.left,
                minWidth: 220,
                background: "#1e1e20",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 10,
                boxShadow: "0 8px 24px rgba(0,0,0,0.55)",
                padding: 4,
                zIndex: 2000,
                display: "flex",
                flexDirection: "column",
                gap: 2,
              }}
            >
              {action.submenu.map((sub) => (
                <button
                  key={sub.label}
                  onClick={() => {
                    setOpenSubmenu(null);
                    void sendPrompt(sub.prompt);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 10px",
                    borderRadius: 6,
                    background: "transparent",
                    border: "none",
                    color: "#e0e0e0",
                    fontSize: 12,
                    fontFamily: "inherit",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "#2a2a2c"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: sub.accent ?? ACCENT, flexShrink: 0 }} />
                  <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                    <span style={{ fontWeight: 500 }}>{sub.label}</span>
                    <span style={{ fontSize: 10, color: "#888" }}>{sub.description}</span>
                  </div>
                </button>
              ))}
            </div>
          </>
        );
      })()}

      <div
        ref={scrollRef}
        style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}
      >
        {messages.length === 0 && (
          <div style={{ color: "#666", fontSize: 12, textAlign: "center", padding: 20, lineHeight: 1.5 }}>
            Beispiel-Anfragen:
            <br />
            <em>„Entferne alle Stille im aktuellen Clip"</em>
            <br />
            <em>„Finde die besten Takes im Interview"</em>
            <br />
            <em>„Schneide bei jedem Sprecherwechsel"</em>
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "84%",
              background: m.role === "user" ? BG_USER : m.role === "system" ? "rgba(255,150,80,0.14)" : BG_ASSISTANT,
              color: m.role === "system" ? "#ffb87a" : "#e6e6e6",
              padding: "8px 12px",
              borderRadius: 10,
              lineHeight: 1.45,
              whiteSpace: "pre-wrap",
              border: m.role === "system" ? "1px solid rgba(255,150,80,0.3)" : "none",
            }}
          >
            {/* Trace ReAct pendant/après le stream (thought/action/observation live) */}
            {m.role === "assistant" && m.steps && m.steps.length > 0 && (
              <StreamingSteps steps={m.steps} collapsed={!m.isStreaming} />
            )}
            {/* Phase muette avant le 1er event : Ollama pense (peut prendre 15-30s
                sur qwen2.5:14b cold-start). On montre un spinner + timer pour que
                le user sache que l'agent travaille. */}
            {m.role === "assistant" && m.isStreaming && (!m.steps || m.steps.length === 0) && !m.content && (
              <PreStreamIndicator startedAt={m.createdAt} />
            )}
            {m.content}
            {m.role === "assistant" && m.isStreaming && m.content && (
              <span style={{ display: "inline-block", width: 6, height: 12, background: "#e5c100", marginLeft: 3, animation: "cinBlink 0.9s steps(2) infinite", verticalAlign: "middle" }} />
            )}
            {m.role === "assistant" && m.proposalId && <ProposalActions proposalId={m.proposalId} />}
            {m.role === "assistant" && m.proactive && m.proactive.length > 0 && (
              <ProactiveSuggestions
                suggestions={m.proactive}
                onPick={(prompt) => sendPrompt(prompt)}
              />
            )}
            {m.role === "assistant" && !m.isStreaming && m.latency && (
              <LatencyBadge stats={m.latency} />
            )}
          </div>
        ))}
        {isPending && !messages.some((m) => m.isStreaming) && (
          <div style={{ alignSelf: "flex-start", color: "#888", fontSize: 11, fontStyle: "italic", padding: "4px 8px", lineHeight: 1.5 }}>
            Verbinde mit Agent…
          </div>
        )}
        <style>{`@keyframes cinBlink { 50% { opacity: 0; } }`}</style>
      </div>
      <div
        style={{
          padding: 10,
          borderTop: "1px solid rgba(255,255,255,0.08)",
          display: "flex",
          gap: 8,
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Frag den Assistenten… (Enter = senden, Shift+Enter = Zeilenumbruch)"
          disabled={isPending}
          rows={2}
          style={{
            flex: 1,
            background: BG_INPUT,
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 8,
            padding: "6px 10px",
            color: "#e6e6e6",
            fontSize: 13,
            resize: "none",
            fontFamily: "inherit",
            outline: "none",
          }}
        />
        <button
          onClick={() => void send()}
          disabled={isPending || !input.trim()}
          style={{
            background: !isPending && input.trim() ? ACCENT : "#333",
            color: !isPending && input.trim() ? "#000" : "#888",
            border: "none",
            borderRadius: 8,
            padding: "0 14px",
            cursor: !isPending && input.trim() ? "pointer" : "not-allowed",
            fontWeight: 600,
            fontSize: 12,
          }}
        >
          Senden
        </button>
      </div>
      {stylePrefsOpen && <StylePrefsModal onClose={() => setStylePrefsOpen(false)} />}
    </div>
  );
}

/**
 * Modale des préférences de style — persiste dans le store Zustand
 * `useStylePrefsStore` (localStorage). Injecté dans chaque appel agent via
 * `timeline_state.style_prefs`.
 */
function StylePrefsModal({ onClose }: { onClose: () => void }) {
  const prefs = useStylePrefsStore((s) => s.prefs);
  const setPref = useStylePrefsStore((s) => s.setPref);
  const setFramingMix = useStylePrefsStore((s) => s.setFramingMix);
  const reset = useStylePrefsStore((s) => s.reset);

  // Slider framing mix : maintient une somme de 100 en ajustant les 2 autres
  // proportionnellement quand on bouge un slider.
  const setFraming = (which: keyof typeof prefs.framing_mix, value: number) => {
    const clamped = Math.max(0, Math.min(100, Math.round(value)));
    const others = (["closeup", "medium", "wide"] as const).filter((k) => k !== which);
    const remaining = 100 - clamped;
    const oldOthersSum = others.reduce((a, k) => a + prefs.framing_mix[k], 0) || 1;
    const newMix = { ...prefs.framing_mix, [which]: clamped };
    for (const k of others) {
      newMix[k] = Math.round((prefs.framing_mix[k] / oldOthersSum) * remaining);
    }
    // Corrige les arrondis pour totaliser 100 exactement.
    const total = newMix.closeup + newMix.medium + newMix.wide;
    if (total !== 100) newMix[others[0]] += 100 - total;
    setFramingMix(newMix);
  };

  return (
    <div
      onClick={onClose}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
      tabIndex={-1}
      ref={(el) => { if (el) el.focus(); }}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)", backdropFilter: "blur(2px)", zIndex: 500, display: "flex", alignItems: "center", justifyContent: "center", outline: "none", padding: 20 }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ width: 480, maxWidth: "100%", maxHeight: "90vh", background: "#161617", borderRadius: 14, border: "1px solid #232326", boxShadow: "0 20px 60px rgba(0,0,0,.75)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "14px 20px 12px", display: "flex", alignItems: "center", gap: 10, borderBottom: "1px solid #232326" }}>
          <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2l-.3-2.5H10.7l-.3 2.5a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.3 2.5h2.6l.3-2.5a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" />
          </svg>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: "#f0f0f0" }}>Stil-Präferenzen</div>
            <div style={{ fontSize: 11, color: "#7a7a7a", marginTop: 2 }}>Der Agent berücksichtigt diese Werte in allen Vorschlägen.</div>
          </div>
          <button onClick={onClose} title="Schließen" style={{ background: "transparent", border: "none", color: "#8a8a8a", cursor: "pointer", padding: 4, display: "flex" }}>
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>
        <div style={{ padding: "16px 20px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 16, fontSize: 12, color: "#cfcfcf" }}>
          {/* Langue */}
          <PrefRow label="Sprache" hint="Antwortsprache des Agenten">
            <div style={{ display: "flex", gap: 4 }}>
              {(["de", "en", "fr"] as Language[]).map((lang) => (
                <button
                  key={lang}
                  onClick={() => setPref("language", lang)}
                  style={{ flex: 1, padding: "6px 8px", borderRadius: 5, background: prefs.language === lang ? ACCENT : "#1c1c1e", color: prefs.language === lang ? "#1a1a1c" : "#c4c4c4", border: `1px solid ${prefs.language === lang ? ACCENT : "#2a2a2c"}`, fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", textTransform: "uppercase" }}
                >
                  {lang}
                </button>
              ))}
            </div>
          </PrefRow>

          {/* Durée cible */}
          <PrefRow label="Ziel-Dauer Rohschnitt" hint={`${prefs.target_duration_sec}s`}>
            <input type="range" min={30} max={600} step={15} value={prefs.target_duration_sec} onChange={(e) => setPref("target_duration_sec", parseInt(e.target.value, 10))} style={{ width: "100%", accentColor: ACCENT }} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#7a7a7a", fontFamily: "ui-monospace, monospace", marginTop: 2 }}>
              <span>30s</span><span>2min</span><span>5min</span><span>10min</span>
            </div>
          </PrefRow>

          {/* Cadence */}
          <PrefRow label="Schnittrhythmus" hint="Cadence des cuts">
            <div style={{ display: "flex", gap: 4 }}>
              {([
                ["fast", "Schnell", "2-4s"],
                ["moderate", "Moderat", "5-8s"],
                ["slow", "Ruhig", "10s+"],
              ] as const).map(([val, label, hint]) => (
                <button
                  key={val}
                  onClick={() => setPref("cutting_style", val as CuttingStyle)}
                  style={{ flex: 1, padding: "6px 8px", borderRadius: 5, background: prefs.cutting_style === val ? ACCENT : "#1c1c1e", color: prefs.cutting_style === val ? "#1a1a1c" : "#c4c4c4", border: `1px solid ${prefs.cutting_style === val ? ACCENT : "#2a2a2c"}`, fontSize: 11, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" }}
                >
                  <div>{label}</div>
                  <div style={{ fontSize: 9, opacity: 0.75, marginTop: 1 }}>{hint}</div>
                </button>
              ))}
            </div>
          </PrefRow>

          {/* Framing mix */}
          <PrefRow label="Framing-Mix" hint="Répartition préférée pour rough cuts (total = 100%)">
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(["closeup", "medium", "wide"] as const).map((k) => (
                <div key={k} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 70, fontSize: 11, color: "#c4c4c4" }}>{k === "closeup" ? "Close-up" : k === "medium" ? "Medium" : "Wide"}</span>
                  <input type="range" min={0} max={100} step={5} value={prefs.framing_mix[k]} onChange={(e) => setFraming(k, parseInt(e.target.value, 10))} style={{ flex: 1, accentColor: ACCENT }} />
                  <span style={{ width: 36, textAlign: "right", fontSize: 11, fontFamily: "ui-monospace, monospace", color: "#c4c4c4" }}>{prefs.framing_mix[k]}%</span>
                </div>
              ))}
            </div>
          </PrefRow>

          {/* Toggles */}
          <PrefRow label="Automatik" hint="Vorschläge, die der Agent selbständig triggert">
            <ToggleRow label="Stille automatisch entfernen" checked={prefs.auto_cleanup_silences} onChange={(v) => setPref("auto_cleanup_silences", v)} />
            <ToggleRow label="Zögerungen (äh, ähm) automatisch entfernen" checked={prefs.auto_remove_hesitations} onChange={(v) => setPref("auto_remove_hesitations", v)} />
            <ToggleRow label="Proaktive Vorschläge nach Ingest" checked={prefs.suggest_proactively} onChange={(v) => setPref("suggest_proactively", v)} />
          </PrefRow>

          {/* Mindestszenenlänge */}
          <PrefRow label="Mindestszenenlänge" hint={`${prefs.min_scene_duration_sec.toFixed(1)}s`}>
            <input type="range" min={0.2} max={5} step={0.1} value={prefs.min_scene_duration_sec} onChange={(e) => setPref("min_scene_duration_sec", parseFloat(e.target.value))} style={{ width: "100%", accentColor: ACCENT }} />
          </PrefRow>
        </div>
        <div style={{ padding: "12px 20px 16px", display: "flex", gap: 8, justifyContent: "space-between", borderTop: "1px solid #232326" }}>
          <button
            onClick={reset}
            style={{ padding: "6px 12px", borderRadius: 6, background: "transparent", color: "#8a8a8a", border: "1px solid #2a2a2c", fontSize: 11, cursor: "pointer", fontFamily: "inherit" }}
          >
            Standardwerte
          </button>
          <button
            onClick={onClose}
            style={{ padding: "6px 14px", borderRadius: 6, background: ACCENT, color: "#1a1a1c", border: "none", fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}
          >
            Fertig
          </button>
        </div>
      </div>
    </div>
  );
}

function PrefRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <div style={{ fontSize: 11, color: "#e0e0e0", fontWeight: 600 }}>{label}</div>
        {hint && <div style={{ fontSize: 10, color: "#7a7a7a", fontFamily: hint.match(/^\d/) ? "ui-monospace, monospace" : "inherit" }}>{hint}</div>}
      </div>
      {children}
    </div>
  );
}

function ToggleRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", cursor: "pointer", fontSize: 11, color: "#c4c4c4" }}>
      <span
        onClick={() => onChange(!checked)}
        role="checkbox"
        aria-checked={checked}
        style={{ width: 30, height: 16, borderRadius: 8, background: checked ? "#3a7c2a" : "#2a2a2c", position: "relative", flex: "none", transition: "background 0.15s" }}
      >
        <span style={{ position: "absolute", top: 2, left: checked ? 16 : 2, width: 12, height: 12, borderRadius: "50%", background: "#fff", transition: "left 0.15s" }} />
      </span>
      {label}
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ display: "none" }} />
    </label>
  );
}

