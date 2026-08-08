"use client";

/**
 * CinAssist — useJobStatus Hook
 *
 * WebSocket-Hook für Echtzeit Job-Fortschritt.
 * Verbindet sich mit ws://localhost:8001/ws/jobs/{jobId}
 */

import { useEffect, useRef, useState, useCallback } from "react";

export interface JobStatus {
  status: "wartend" | "laeuft" | "fertig" | "fehler";
  progress: number;
  message: string;
  result?: Record<string, unknown>;
}

const INITIAL_STATUS: JobStatus = {
  status: "wartend",
  progress: 0,
  message: "Verbinde...",
};

export function useJobStatus(jobId: string | null) {
  const [jobStatus, setJobStatus] = useState<JobStatus>(INITIAL_STATUS);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
  }, []);

  useEffect(() => {
    if (!jobId) {
      setJobStatus(INITIAL_STATUS);
      return;
    }

    const wsBase =
      process.env.NEXT_PUBLIC_WS_URL ||
      (typeof window !== "undefined" && location.hostname !== "localhost"
        ? `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`
        : "ws://localhost:8001");
    const ws = new WebSocket(`${wsBase}/ws/jobs/${jobId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setJobStatus({ ...INITIAL_STATUS, message: "Verbunden. Warte auf Updates..." });
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setJobStatus({
          status: data.status || "laeuft",
          progress: data.progress ?? 0,
          message: data.message || "",
          result: data.result,
        });
      } catch {
        // Ungültige Nachricht ignorieren
      }
    };

    ws.onerror = () => {
      setJobStatus({
        status: "fehler",
        progress: 0,
        message: "WebSocket-Verbindungsfehler.",
      });
    };

    ws.onclose = () => {
      setConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [jobId]);

  return { jobStatus, connected, disconnect };
}
