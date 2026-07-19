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
import { useChatStore } from "@/lib/chat-store";
import { useProposalStore } from "@/lib/proposals";
import type { TimelineCmd } from "@/lib/timeline-commands";

const ACCENT = "#e5c100";
const BG_PANEL = "#1c1c1e";
const BG_INPUT = "#242426";
const BG_USER = "#2f4a70";
const BG_ASSISTANT = "#232326";

function ProposalActions({ proposalId }: { proposalId: string }) {
  const proposal = useProposalStore((s) => s.proposals.find((p) => p.id === proposalId));
  const acceptProposal = useProposalStore((s) => s.acceptProposal);
  const rejectProposal = useProposalStore((s) => s.rejectProposal);
  if (!proposal) return null;

  const nEdits = proposal.edits.length;
  const nLabel = `${nEdits} Aktion${nEdits > 1 ? "en" : ""}`;

  if (proposal.status === "accepted") {
    return (
      <div style={{ marginTop: 10, padding: "6px 10px", background: "rgba(120,200,120,0.14)", border: "1px solid rgba(120,200,120,0.3)", borderRadius: 6, fontSize: 11, color: "#96d996" }}>
        ✓ Angenommen · {nLabel}
      </div>
    );
  }
  if (proposal.status === "rejected") {
    return (
      <div style={{ marginTop: 10, padding: "6px 10px", background: "rgba(200,120,120,0.14)", border: "1px solid rgba(200,120,120,0.3)", borderRadius: 6, fontSize: 11, color: "#d99696" }}>
        ✕ Abgelehnt
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

type AgentReply = {
  reply: string;
  proposals?: Array<{
    title: string;
    summary?: string;
    edits: TimelineCmd[];
    provenance?: {
      tool: string;
      params?: Record<string, unknown>;
      agentThought?: string;
    };
  }>;
};

export default function ChatPanel() {
  const [input, setInput] = useState("");
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
    try {
      const executor = useProposalStore.getState().executor;
      const timeline_state = executor?.getSnapshot();
      const res = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, timeline_state }),
      });
      // Lire toujours le body — la route proxy renvoie un `reply` explicite
      // même en cas d'erreur (backend down, timeout…). Ne pas throw juste sur
      // res.ok sinon on perd le message utile.
      let data: AgentReply | null = null;
      try {
        data = (await res.json()) as AgentReply;
      } catch {
        // pas de JSON du tout → fallback message générique
      }
      if (!data || (!res.ok && !data.reply)) {
        throw new Error(`HTTP ${res.status} ${res.statusText || ""}`.trim());
      }
      if (!res.ok) {
        // status non-OK mais body contient un reply → afficher en message system
        chat.addMessage({ role: "system", content: data.reply });
        return;
      }
      // Create proposals FIRST so we have their ids to attach to the assistant
      // message. This way <ProposalActions> can look up the proposal live from
      // the store and re-render on status changes.
      let firstProposalId: string | undefined;
      if (data.proposals?.length) {
        const propStore = useProposalStore.getState();
        for (const p of data.proposals) {
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
      chat.addMessage({ role: "assistant", content: data.reply, proposalId: firstProposalId });
    } catch (err) {
      chat.addMessage({ role: "system", content: `Fehler beim Agent-Aufruf: ${(err as Error).message}` });
    } finally {
      chat.setPending(false);
    }
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
        bottom: 20,
        right: 20,
        width: 400,
        height: 520,
        background: BG_PANEL,
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 12,
        boxShadow: "0 12px 40px rgba(0,0,0,0.55)",
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
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => useChatStore.getState().clear()}
            title="Verlauf löschen"
            style={{ background: "transparent", border: "none", color: "#888", cursor: "pointer", fontSize: 11, padding: "2px 6px" }}
          >
            leeren
          </button>
          <button
            onClick={() => setOpen(false)}
            style={{ background: "transparent", border: "none", color: "#aaa", cursor: "pointer", fontSize: 16, padding: "0 6px" }}
          >
            ✕
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
            {m.content}
            {m.role === "assistant" && m.proposalId && <ProposalActions proposalId={m.proposalId} />}
          </div>
        ))}
        {isPending && (
          <div style={{ alignSelf: "flex-start", color: "#888", fontSize: 11, fontStyle: "italic", padding: "4px 8px", lineHeight: 1.5 }}>
            Agent denkt… <br />
            <span style={{ fontSize: 10, color: "#666" }}>qwen2.5:14b lokal · kann 1–3 min dauern</span>
          </div>
        )}
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
    </div>
  );
}
