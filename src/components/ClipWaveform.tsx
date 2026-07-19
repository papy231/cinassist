"use client";

// ClipWaveform.tsx — waveform dynamique par clip audio via wavesurfer.js (v7).
//
// Layer 3 (compositeur / UI), display-only. `interact: false` : ce composant ne
// pilote JAMAIS la lecture. La timeline reste la seule source de vérité.
//
// Perf : les peaks sont décodés une fois par source (wave-peaks-cache) puis
// partagés. Ce composant ne fait que découper sa sous-fenêtre et la dessiner.

import { memo, useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import { getPeaks, slicePeaks } from "@/lib/wave-peaks-cache";

type Props = {
  src: string;
  mediaStart: number;    // offset dans la source (s)
  duration: number;      // longueur du segment (s)
  sourceDuration: number; // longueur totale de la source (s)
  color?: string;
  height?: number;       // hauteur en px ; suit le redimensionnement de la piste
  fallbackUrl?: string;  // PNG existant, affiché si le décodage échoue
};

type Status = "loading" | "ready" | "error";

function ClipWaveform({
  src,
  mediaStart,
  duration,
  sourceDuration,
  color = "#d0f5da",
  height = 40,
  fallbackUrl,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  // (Re)crée le waveform quand la source, la fenêtre ou la hauteur changent.
  // Debouncé à 200ms pour éviter une boucle setState pendant un drag/trim
  // rapide (chaque frame → setTlClips → props changent → useEffect re-run →
  // setStatus → re-render → boucle infinie "Maximum update depth exceeded").
  useEffect(() => {
    let cancelled = false;

    const renderWave = () => {
      // Détruit une instance précédente avant d'en recréer une.
      if (wsRef.current) {
        wsRef.current.destroy();
        wsRef.current = null;
      }

      if (!src) {
        setStatus("error");
        return;
      }

      setStatus("loading");

      getPeaks(src)
        .then((fullPeaks) => {
          if (cancelled || !containerRef.current) return;
          if (fullPeaks.length === 0) {
            setStatus("error"); // peaks indécodables → fallback PNG
            return;
          }
          const sliced = slicePeaks(
            fullPeaks,
            mediaStart,
            duration,
            sourceDuration
          );

          const ws = WaveSurfer.create({
            container: containerRef.current,
            peaks: [sliced],
            duration: duration > 0 ? duration : 1,
            waveColor: color,
            progressColor: color,
            cursorWidth: 0,
            interact: false,      // display-only, ne pilote pas la lecture
            normalize: true,
            barWidth: 1.5,
            barGap: 0.5,
            barRadius: 1,
            height: height,
          });
          wsRef.current = ws;
          setStatus("ready");
        })
        .catch((err) => {
          if (cancelled) return;
          console.warn("[ClipWaveform] render failed:", err);
          setStatus("error");
        });
    };

    const debounceId = setTimeout(() => {
      if (cancelled) return;
      renderWave();
    }, 200);

    return () => {
      cancelled = true;
      clearTimeout(debounceId);
      if (wsRef.current) {
        wsRef.current.destroy();
        wsRef.current = null;
      }
    };
  }, [src, mediaStart, duration, sourceDuration, color, height]);

  // Erreur → fallback PNG (même style que l'ancien rendu DaVinci).
  if (status === "error") {
    if (fallbackUrl) {
      return (
        <div
          style={{
            position: "absolute",
            top: 18,
            bottom: 2,
            left: 0,
            right: 0,
            backgroundImage: `url(${fallbackUrl})`,
            backgroundSize: "100% 100%",
            backgroundRepeat: "no-repeat",
            opacity: 1,
            filter: "brightness(1.35) contrast(1.15) saturate(0.85)",
            pointerEvents: "none",
          }}
        />
      );
    }
    // Pas de PNG non plus → laisser le caller montrer son SVG de secours.
    return null;
  }

  return (
    <div
      style={{
        position: "absolute",
        top: 18,
        bottom: 2,
        left: 0,
        right: 0,
        pointerEvents: "none",
      }}
    >
      {/* Skeleton pulse pendant le décodage (premier montage par source). */}
      {status === "loading" && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            borderRadius: 4,
            background:
              "linear-gradient(90deg, rgba(208,245,218,0.08), rgba(208,245,218,0.18), rgba(208,245,218,0.08))",
            animation: "cinassistWavePulse 1.2s ease-in-out infinite",
          }}
        />
      )}
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <style>{`@keyframes cinassistWavePulse {0%,100%{opacity:.4}50%{opacity:1}}`}</style>
    </div>
  );
}

// Wavesurfer décode + rend un canvas par instance : c'est lourd. Editor.tsx se
// re-rend à 60 fps pendant la lecture (playhead). memo() empêche de re-exécuter
// ce composant tant que ses props (toutes primitives + stables) ne changent pas,
// donc l'instance wavesurfer ne clignote/thrash pas. Golden rule : display-only.
export default memo(ClipWaveform);
