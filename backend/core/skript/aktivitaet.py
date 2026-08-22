"""Aktivitätskurve eines Clips (Bildbewegung über die Zeit) — für das Trimmen stummer Takes im Rohschnitt.

Frame-Differenz auf einem 32×18-Graubild, 2 Messungen/s, aus dem Proxy (schnell, ~1–2 s pro Take). Kein Modell.
Ergebnis: Liste von (t, aktivität 0–255); daraus `aktives_fenster()` = Zeitraum, in dem wirklich etwas passiert
(nach Klappe/Einrichten, vor dem Stehenbleiben am Ende) und `bestes_fenster()` = das aktivste Fenster einer Maximallänge.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache

from backend.core.config import FFMPEG_BIN

FPS = 2
W, H = 96, 54
PIXEL_SCHWELLE = 14      # |Δ| über diesem Wert = „bewegtes Pixel“ — Maß = Anteil bewegter Pixel (0–100 %)


@lru_cache(maxsize=256)
def kurve(pfad: str, start: float = 0.0, dauer: float | None = None) -> tuple[tuple[float, float], ...]:
    """(t, Anteil bewegter Pixel in %) alle 0,5 s. Leer, wenn ffmpeg scheitert."""
    cmd = [FFMPEG_BIN, "-v", "error", "-ss", f"{start:.2f}", "-i", pfad]
    if dauer:
        cmd += ["-t", f"{dauer:.2f}"]
    cmd += ["-vf", f"fps={FPS},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return ()
    n = W * H
    frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
    out: list[tuple[float, float]] = []
    prev = None
    for k, f in enumerate(frames):
        if prev is not None:
            bewegt = sum(1 for a, b in zip(f, prev) if abs(a - b) > PIXEL_SCHWELLE)
            out.append((start + k / FPS, 100.0 * bewegt / n))
        prev = f
    return tuple(out)


def _glaette(k: list[tuple[float, float]], fenster: int = 5) -> list[tuple[float, float]]:
    if not k:
        return []
    vals = [v for _, v in k]
    out = []
    for i in range(len(vals)):
        lo, hi = max(0, i - fenster // 2), min(len(vals), i + fenster // 2 + 1)
        out.append((k[i][0], sum(vals[lo:hi]) / (hi - lo)))
    return out


def aktives_fenster(k, mindest_s: float = 2.0) -> tuple[float, float] | None:
    """Erster und letzter Zeitpunkt nachhaltiger Bewegung (≥ Schwelle für ≥ mindest_s)."""
    kk = _glaette(list(k))
    if not kk:
        return None
    vals = sorted(v for _, v in kk)
    p90 = vals[int(0.9 * (len(vals) - 1))]
    median = vals[len(vals) // 2]
    # Schwelle: deutlich über dem Grundrauschen (Median), aber nicht nur die Klappe (p90-Abstand)
    schwelle = max(0.4, median + 0.3 * (p90 - median))
    n_min = max(1, int(mindest_s * FPS))
    erst = letzt = None
    lauf = 0
    for i, (t, v) in enumerate(kk):
        if v >= schwelle:
            lauf += 1
            if lauf >= n_min:
                if erst is None:
                    erst = kk[i - n_min + 1][0]
                letzt = t
        else:
            lauf = 0
    if erst is None:
        return None
    return erst, letzt + 1.0 / FPS


def bestes_fenster(k, max_len: float, innerhalb: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Das Fenster der Länge ≤ max_len mit der größten Aktivitätssumme (innerhalb eines Bereichs)."""
    kk = [(t, v) for t, v in k if innerhalb is None or innerhalb[0] <= t <= innerhalb[1]]
    if not kk:
        return None
    if kk[-1][0] - kk[0][0] <= max_len:
        return kk[0][0], kk[-1][0] + 1.0 / FPS
    n = int(max_len * FPS)
    vals = [v for _, v in kk]
    best, best_i = -1.0, 0
    s = sum(vals[:n])
    best, best_i = s, 0
    for i in range(1, len(vals) - n + 1):
        s += vals[i + n - 1] - vals[i - 1]
        if s > best:
            best, best_i = s, i
    return kk[best_i][0], kk[best_i][0] + max_len


def _median(vals: list[float]) -> float:
    v = sorted(vals)
    return v[len(v) // 2] if v else 0.0


def anfang_nach_klappe(k, start: float, such_s: float = 12.0) -> tuple[float, str | None]:
    """Sichtbare Klappe/Hand am Anfang überspringen: kurzer Bewegungs-Spike kurz nach `start`, danach beruhigt sich
    das Bild → neuer Start nach dem Spike. Ohne Spike bleibt `start`. Liefert (start, hinweis)."""
    kk = [(t, v) for t, v in k if t >= start]
    if len(kk) < 4:
        return start, None
    med = _median([v for _, v in kk])
    spike_schwelle = max(5.0, 3.0 * med)
    ruhe = max(1.0, 1.5 * med)
    fenster = [(t, v) for t, v in kk if t <= start + such_s]
    spike = next((t for t, v in fenster if v >= spike_schwelle), None)
    if spike is None:
        return start, None
    # Ende des Spikes: erstes t nach dem Spike mit ≥ 2 ruhigen Messungen (1 s)
    nach = [(t, v) for t, v in kk if t > spike]
    ende = None
    for i in range(len(nach) - 1):
        if nach[i][1] < ruhe and nach[i + 1][1] < ruhe:
            ende = nach[i][0]
            break
        if nach[i][0] - spike > such_s:
            break
    if ende is None:
        ende = spike + 2.0
    return ende + 0.3, f"Klappe/Einrichten bei {spike:.0f} s übersprungen"


def ende_bereinigen(k, start: float, ende: float, ab_anteil: float = 0.75) -> tuple[float, str | None]:
    """Aus-dem-Spiel-Fallen am Ende (Aufstehen, Lachen, Crew ins Bild): kräftiger Bewegungs-Spike im letzten Viertel
    des Fensters → Ende davor. Liefert (ende, hinweis)."""
    kk = [(t, v) for t, v in k if start <= t <= ende]
    if len(kk) < 8:
        return ende, None
    grenze = start + ab_anteil * (ende - start)
    kern = [v for t, v in kk if t < grenze]
    med = _median(kern) if kern else _median([v for _, v in kk])
    schwelle = max(6.0, 3.0 * med + 2.0)
    tail = [(t, v) for t, v in kk if t >= grenze]
    for i in range(len(tail) - 1):
        if tail[i][1] >= schwelle and tail[i + 1][1] >= schwelle * 0.6:
            neu = max(start + 1.0, tail[i][0] - 0.5)
            if neu < ende - 0.5:
                return neu, f"Ende bei {neu:.0f} s — Bewegungssprung (aus dem Spiel gefallen)"
            break
    return ende, None


def hoehepunkte(k, start: float, ende: float, max_fenster: int = 3, fenster_len: float = 8.0, max_gesamt: float = 24.0,
                mindest_abstand: float = 10.0) -> list[tuple[float, float]]:
    """Bis zu `max_fenster` Fenster (je `fenster_len` s) an den stärksten Bewegungsmaxima innerhalb [start, ende],
    chronologisch sortiert, Gesamtdauer ≤ max_gesamt. Für Handlung ohne Dialog im Feinschnitt: Anfang/Höhepunkt/Ende
    statt Block."""
    kk = _glaette([(t, v) for t, v in k if start <= t <= ende], fenster=int(fenster_len * FPS))
    if not kk:
        return []
    n_max = max(1, min(max_fenster, int(max_gesamt // fenster_len)))
    kandidaten = sorted(kk, key=lambda x: -x[1])
    gewaehlt: list[float] = []
    for t, _ in kandidaten:
        if all(abs(t - g) >= mindest_abstand for g in gewaehlt):
            gewaehlt.append(t)
        if len(gewaehlt) >= n_max:
            break
    out = []
    for t in sorted(gewaehlt):
        a = max(start, t - fenster_len / 2); b = min(ende, a + fenster_len)
        a = max(start, b - fenster_len)
        if out and a <= out[-1][1] + 1.0:
            # angrenzend → zu einem Fenster verschmelzen
            out[-1] = (out[-1][0], round(max(out[-1][1], b), 2))
            continue
        if b - a >= 3.0:
            out.append((round(a, 2), round(b, 2)))
    return out
