"use client";

/**
 * CinAssist — ChatPanel
 *
 * Konversationelle Schnitt-Assistenz : der Editor redet mit dem System wie mit
 * einem Kollegen statt einen Stil-Chip anzuklicken.
 *
 * Workflow:
 *   1. Beim Öffnen: ruft /api/ai/chat mit leerer History auf → erste Nachricht
 *      (was sieht das System im Material + offene Frage).
 *   2. User antwortet, ruft /api/ai/chat mit erweiterter History.
 *   3. Wenn die Antwort ein `proposed_prompt` enthält → "Auf Timeline
 *      anwenden"-Button erscheint und triggert die bestehende prompt-driven
 *      Schnitt-Logik (/api/ai/cut).
 *
 * Backend-Endpunkt: backend/api/chat.py
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import type { ClipDTO } from "@/lib/api";

type ChatRole = "user" | "assistant";

interface ChatMsg {
  role: ChatRole;
  content: string;
  proposed_prompt?: string | null;
  proposed_stil?: string | null;
}

interface ChatPanelProps {
  clips: ClipDTO[];
  onProposedPrompt: (prompt: string, stil?: string) => void;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export function ChatPanel({ clips, onProposedPrompt }: ChatPanelProps) {
  const analyzedClips = clips.filter(c => c.status === "analysiert");
  const clipIds = analyzedClips.map(c => c.id);
  const clipIdsKey = clipIds.join("|"); // stable dependency

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const firstLoadDone = useRef(false);

  // ── Auto-scroll bei neuer Nachricht ─────────────
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // ── API-Call ─────────────────────────────────────
  const sendTurn = useCallback(async (history: ChatMsg[]) => {
    if (clipIds.length === 0) return;
    setLoading(true);
    try {
      const r = await fetch(`${API_BASE}/api/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip_ids: clipIds,
          messages: history.map(m => ({ role: m.role, content: m.content })),
        }),
      });
      if (!r.ok) throw new Error(`Chat HTTP ${r.status}`);
      const data = await r.json();
      const assistantMsg: ChatMsg = {
        role: "assistant",
        content: data.message,
        proposed_prompt: data.proposed_prompt,
        proposed_stil: data.proposed_stil,
      };
      setMessages([...history, assistantMsg]);
    } catch (err) {
      setMessages([
        ...history,
        {
          role: "assistant",
          content: `❌ Verbindung zum KI-Assistenten fehlgeschlagen: ${err instanceof Error ? err.message : "unbekannter Fehler"}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipIdsKey]);

  // ── Erste Nachricht automatisch holen ───────────
  useEffect(() => {
    if (clipIds.length === 0) {
      setMessages([]);
      firstLoadDone.current = false;
      return;
    }
    if (firstLoadDone.current) return;
    firstLoadDone.current = true;
    sendTurn([]);
  }, [clipIdsKey, sendTurn, clipIds.length]);

  // ── User sendet Nachricht ───────────────────────
  const handleSend = useCallback(() => {
    const text = draft.trim();
    if (!text || loading) return;
    const newHistory: ChatMsg[] = [...messages, { role: "user", content: text }];
    setMessages(newHistory);
    setDraft("");
    sendTurn(newHistory);
  }, [draft, loading, messages, sendTurn]);

  // ── Empty state (keine Clips) ───────────────────
  if (analyzedClips.length === 0) {
    return (
      <div style={{
        flex: 1, display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        padding: 20, textAlign: "center", color: "var(--text3)",
        fontSize: 11, lineHeight: 1.5,
      }}>
        <svg width={32} height={32} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.2} style={{ opacity: 0.3, marginBottom: 10 }}>
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Noch keine analysierten Clips</div>
        <div>Lade Videos hoch im <b>Clips</b>-Tab.<br/>Sobald die Analyse fertig ist, kannst du mit mir reden.</div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
      {/* ── Header ──────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 7,
        padding: "8px 10px", borderBottom: "1px solid var(--border)",
        background: "var(--bg2)",
      }}>
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#64d9a0" }} />
        <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text)" }}>Schnittassistent</span>
        <span style={{ fontSize: 9, color: "var(--text3)", marginLeft: "auto" }}>
          {analyzedClips.length} Clip{analyzedClips.length !== 1 ? "s" : ""} im Katalog · 100 % lokal
        </span>
      </div>

      {/* ── Messages (scrollbar) ────────────────── */}
      <div ref={scrollRef} style={{
        flex: 1, overflowY: "auto", padding: 10,
        display: "flex", flexDirection: "column", gap: 10,
      }}>
        {messages.length === 0 && loading && (
          <div style={{ color: "var(--text3)", fontSize: 11, fontStyle: "italic", textAlign: "center", padding: 20 }}>
            Schaue mir dein Material an…
          </div>
        )}

        {messages.map((m, i) => {
          // Buttons A/B/C werden NUR auf der letzten Assistenten-Nachricht angezeigt,
          // sodass die Konversation nicht mit alten Auswahlbuttons überladen wird.
          const istLetzte = i === messages.length - 1;
          return (
            <MessageBubble
              key={i}
              role={m.role}
              content={m.content}
              proposedPrompt={m.proposed_prompt ?? null}
              proposedStil={m.proposed_stil ?? null}
              onApply={(p, s) => onProposedPrompt(p, s ?? undefined)}
              istLetzteAssistantNachricht={istLetzte && m.role === "assistant" && !loading}
              onAuswahl={(text) => {
                // Wenn der User auf einen Vorschlag-Button klickt, simulieren wir
                // einen Sende-Vorgang: die Auswahl wird als User-Antwort gespeichert
                // und der Backend-Call läuft.
                const newHistory: ChatMsg[] = [...messages, { role: "user", content: text }];
                setMessages(newHistory);
                sendTurn(newHistory);
              }}
            />
          );
        })}

        {loading && messages.length > 0 && (
          <div style={{ fontSize: 11, color: "var(--text3)", fontStyle: "italic", paddingLeft: 8 }}>
            schreibt…
          </div>
        )}
      </div>

      {/* ── Input ───────────────────────────────── */}
      <div style={{
        borderTop: "1px solid var(--border)",
        padding: 8, display: "flex", gap: 6, alignItems: "flex-end",
        background: "var(--bg1)",
      }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Antworte hier…   (Enter = senden, Shift+Enter = neue Zeile)"
          rows={2}
          disabled={loading}
          style={{
            flex: 1, resize: "none",
            background: "var(--bg3)", border: "1px solid var(--border)",
            borderRadius: 6, padding: "6px 8px",
            fontSize: 11, color: "var(--text)",
            fontFamily: "var(--font)", lineHeight: 1.4,
            opacity: loading ? 0.5 : 1,
          }}
        />
        <button
          onClick={handleSend}
          disabled={loading || !draft.trim()}
          style={{
            width: 32, height: 32, flexShrink: 0,
            background: draft.trim() && !loading ? "var(--orange)" : "var(--bg4)",
            border: "none", borderRadius: 6, cursor: draft.trim() && !loading ? "pointer" : "not-allowed",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "white",
          }}
          title="Senden (Enter)"
        >
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
            <line x1={22} y1={2} x2={11} y2={13}/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </button>
      </div>
    </div>
  );
}


// ─── Message Bubble ─────────────────────────────────

// Extrahiert "A) ... B) ... C) ..." als klickbare Auswahl-Optionen.
// Diese Liste wird nur unter der letzten Assistenten-Nachricht angezeigt.
function extrahiereAuswahl(text: string): { letter: string; label: string }[] {
  // Muster: am Zeilenanfang "A)" / "B)" / "C)" / "D)" gefolgt von Text bis Zeilenende.
  const matches: { letter: string; label: string }[] = [];
  const re = /^\s*([A-D])\)\s+([^\n]{4,200})/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const letter = m[1];
    let label = m[2].trim();
    // Lange Optionen abkürzen für den Button
    if (label.length > 64) label = label.slice(0, 62) + "…";
    matches.push({ letter, label });
  }
  // Nur valide ab 2 Optionen
  return matches.length >= 2 ? matches.slice(0, 4) : [];
}

function MessageBubble({
  role,
  content,
  proposedPrompt,
  proposedStil,
  onApply,
  istLetzteAssistantNachricht,
  onAuswahl,
}: {
  role: ChatRole;
  content: string;
  proposedPrompt: string | null;
  proposedStil?: string | null;
  onApply: (prompt: string, stil?: string | null) => void;
  istLetzteAssistantNachricht?: boolean;
  onAuswahl?: (text: string) => void;
}) {
  const isUser = role === "user";
  const auswahl = !isUser && istLetzteAssistantNachricht ? extrahiereAuswahl(content) : [];
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: isUser ? "flex-end" : "flex-start",
      gap: 4,
    }}>
      <div style={{
        maxWidth: "85%",
        padding: "7px 11px",
        borderRadius: 10,
        borderTopLeftRadius: isUser ? 10 : 2,
        borderTopRightRadius: isUser ? 2 : 10,
        background: isUser ? "rgba(249,115,22,0.18)" : "var(--bg3)",
        border: isUser ? "1px solid rgba(249,115,22,0.35)" : "1px solid var(--border)",
        color: "var(--text)",
        fontSize: 11.5,
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}>
        {content}
      </div>

      {/* Klickbare Auswahl-Optionen (A/B/C) — erscheinen NUR unter der letzten
          Assistenten-Nachricht. Macht den geführten Dialog ein-Klick statt
          tippen. Klick = sendet "Ich entscheide mich für X..." an den Chat. */}
      {!isUser && auswahl.length > 0 && onAuswahl && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 4 }}>
          {auswahl.map(opt => (
            <button
              key={opt.letter}
              onClick={() => onAuswahl(`Ich entscheide mich für ${opt.letter}: ${opt.label}`)}
              style={{
                padding: "5px 10px",
                background: "rgba(249,115,22,.12)",
                color: "var(--orange)",
                border: "1px solid rgba(249,115,22,.35)",
                borderRadius: 16,
                fontSize: 10.5,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "var(--font)",
                display: "flex", alignItems: "center", gap: 5,
                maxWidth: "100%",
              }}
              onMouseEnter={e => { e.currentTarget.style.background = "rgba(249,115,22,.22)"; }}
              onMouseLeave={e => { e.currentTarget.style.background = "rgba(249,115,22,.12)"; }}
              title={`Auswahl: ${opt.label}`}
            >
              <span style={{ fontWeight: 800, opacity: 0.85 }}>{opt.letter}</span>
              <span style={{
                fontWeight: 500,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 240,
              }}>{opt.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Wenn der Assistent einen Schnitt-Vorschlag macht: Apply-Button */}
      {!isUser && proposedPrompt && (
        <button
          onClick={() => onApply(proposedPrompt, proposedStil)}
          style={{
            marginTop: 2,
            padding: "5px 12px",
            background: "var(--green)",
            color: "white",
            border: "none",
            borderRadius: 6,
            fontSize: 10,
            fontWeight: 700,
            cursor: "pointer",
            fontFamily: "var(--font)",
            display: "flex",
            alignItems: "center",
            gap: 5,
          }}
          title={`Erzeuge Cut mit Prompt: "${proposedPrompt}"`}
        >
          <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
          Auf Timeline anwenden
          {proposedStil && (
            <span style={{
              marginLeft: 4, padding: "1px 6px", borderRadius: 8,
              background: "rgba(255,255,255,.2)", fontSize: 9, fontWeight: 600,
            }}>
              {proposedStil}
            </span>
          )}
        </button>
      )}
    </div>
  );
}
