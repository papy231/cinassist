"""Zeilen-Alignment: gesprochene Spiel-Sätze eines Takes ↔ Dialogzeilen einer Skriptszene.

Sprachübergreifend (Skript englisch, Dreh deutsch): Ähnlichkeit = Kosinus der **bge-m3**-Embeddings (Ollama,
lokal; gemessen: passende Paare 0,78–0,84, fremde 0,39–0,43). Zusätzlich lexikalische Ähnlichkeit gegen die
deutsche Übersetzung der Skriptzeile (falls vorhanden) — das Maximum zählt.
Zuordnung = argmax je Satz mit Schwelle (+ kleiner Reihenfolge-Bonus) — NICHT streng monoton, weil Schauspieler
Zeilen wiederholen und Whisper Zeilen zerteilt (Messung am Korpus: DP verlor echte Treffer).
Deterministisch; der einzige „Modell“-Anteil ist das Embedding, und das ist reproduzierbar.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher

EMBED_MODEL = "bge-m3"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
SCHWELLE = 0.55             # unter dieser Ähnlichkeit gilt ein Satz als nicht im Skript (Improvisation/Zusatz)

_cache: dict[str, list[float]] = {}


def _key(t: str) -> str:
    return hashlib.sha1((EMBED_MODEL + "\x00" + t).encode("utf-8")).hexdigest()


def embed(texte: list[str], timeout: float = 120.0) -> list[list[float] | None]:
    """Embeddings (gecacht pro Prozess). None je Eintrag, wenn Ollama/Modell fehlt."""
    out: list[list[float] | None] = [None] * len(texte)
    offen = [(i, t) for i, t in enumerate(texte) if t and _key(t) not in _cache]
    for i, t in enumerate(texte):
        if t and _key(t) in _cache:
            out[i] = _cache[_key(t)]
    if offen:
        try:
            body = json.dumps({"model": EMBED_MODEL, "input": [t for _, t in offen], "keep_alive": "5m"}).encode()
            req = urllib.request.Request(OLLAMA_EMBED_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                embs = json.loads(r.read()).get("embeddings") or []
            for (i, t), e in zip(offen, embs):
                _cache[_key(t)] = e
                out[i] = e
        except Exception:  # noqa: BLE001 — ohne Embeddings fällt die Zuordnung auf Lexik zurück
            pass
    return out


def _cos(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def _norm(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[^a-zäöüß0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def lexikalisch(a: str, b: str) -> float:
    """Token-Jaccard + Zeichen-Ratio (für DE↔DE-Übersetzung)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb)
    return 0.5 * jac + 0.5 * SequenceMatcher(None, na, nb).ratio()


@dataclass
class SkriptZeileRef:
    id: str
    nr: int
    figur: str | None
    text: str
    text_ziel: str | None = None


@dataclass
class Zuordnung:
    einheit_idx: int
    zeile_id: str | None
    zeile_nr: int | None
    score: float


@dataclass
class AlignmentErgebnis:
    zuordnungen: list[Zuordnung]
    gedeckt: dict[str, float]            # zeile_id → bester Score (nur Treffer ≥ Schwelle)
    abdeckung: float                     # gedeckte Dialogzeilen / alle Dialogzeilen
    szenen_score: float                  # mittlere Ähnlichkeit der zugeordneten Sätze (wie gut passt der Take zur Szene)
    matrix: list[list[float]] = field(default_factory=list)


def aehnlichkeit_matrix(saetze: list[str], zeilen: list[SkriptZeileRef]) -> list[list[float]]:
    e_s = embed(saetze)
    e_z = embed([z.text for z in zeilen])
    e_zz = embed([z.text_ziel or "" for z in zeilen])
    m: list[list[float]] = []
    for i, s in enumerate(saetze):
        row = []
        for j, z in enumerate(zeilen):
            sem = max(_cos(e_s[i], e_z[j]), _cos(e_s[i], e_zz[j]) if z.text_ziel else 0.0)
            lex = lexikalisch(s, z.text_ziel) if z.text_ziel else 0.0
            row.append(max(sem, 0.85 * lex + 0.15 * sem))
        m.append(row)
    return m


def _schwelle_fuer(satz: str, schwelle: float) -> float:
    """Sehr kurze Einheiten („Babe.“, „Hallo.“) haben rauschige Embeddings → strengere Schwelle."""
    n = len(_norm(satz).split())
    if n <= 1:
        return max(schwelle, 0.72)
    if n <= 2:
        return max(schwelle, 0.66)
    return schwelle


def _dp_monoton(M: list[list[float]], saetze: list[str], zeilen: list[SkriptZeileRef], schwelle: float) -> list[int | None]:
    """Monotone Zuordnung (Reihenfolge der Skriptzeilen), Sätze dürfen ausgelassen werden, dieselbe Zeile darf
    von mehreren aufeinanderfolgenden Sätzen getroffen werden. Liefert je Satz den Zeilenindex oder None."""
    n, m = len(saetze), len(zeilen)
    NEG = -1e9
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    bt: list[list[tuple[int, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n):
        th = _schwelle_fuer(saetze[i], schwelle)
        for j in range(m + 1):
            if dp[i][j] == NEG:
                continue
            if dp[i][j] > dp[i + 1][j]:
                dp[i + 1][j] = dp[i][j]; bt[i + 1][j] = (i, j, -1)
            for k in range(max(0, j - 1), m):
                sc = M[i][k]
                if sc < max(th, _schwelle_fuer(zeilen[k].text, schwelle)):
                    continue
                val = dp[i][j] + sc
                if val > dp[i + 1][k + 1]:
                    dp[i + 1][k + 1] = val; bt[i + 1][k + 1] = (i, j, k)
    j = max(range(m + 1), key=lambda jj: dp[n][jj])
    out: list[int | None] = [None] * n
    i = n
    while i > 0 and bt[i][j] is not None:
        pi, pj, k = bt[i][j]
        if k >= 0:
            out[i - 1] = k
        i, j = pi, pj
    return out


def aligne(saetze: list[str], zeilen: list[SkriptZeileRef], schwelle: float = SCHWELLE) -> AlignmentErgebnis:
    """Zuordnung Satz → Skriptzeile = Konsens aus (a) monotoner DP (Skript-Reihenfolge, robust gegen ähnliche Zeilen
    an anderer Stelle) und (b) sehr sicheren argmax-Treffern (≥ 0,72, beide Seiten ≥ 3 Wörter — fängt Wiederholungen
    früherer Zeilen und Whisper-Zerteilung). Kurze Einheiten/Zeilen brauchen höhere Schwellen (rauschige Embeddings)."""
    if not saetze or not zeilen:
        return AlignmentErgebnis([], {}, 0.0, 0.0, [])
    M = aehnlichkeit_matrix(saetze, zeilen)
    m = len(zeilen)
    dp_hits = _dp_monoton(M, saetze, zeilen, schwelle)
    zu: list[Zuordnung] = []
    for i, satz in enumerate(saetze):
        k = dp_hits[i]
        if k is None:
            kk = max(range(m), key=lambda x: M[i][x])
            sicher = (M[i][kk] >= 0.72 and len(_norm(satz).split()) >= 3 and len(_norm(zeilen[kk].text).split()) >= 3)
            k = kk if sicher else None
            if k is None:
                zu.append(Zuordnung(i, None, None, round(M[i][kk], 3)))
                continue
        zu.append(Zuordnung(i, zeilen[k].id, zeilen[k].nr, round(M[i][k], 3)))
    gedeckt: dict[str, float] = {}
    for z in zu:
        if z.zeile_id:
            gedeckt[z.zeile_id] = max(gedeckt.get(z.zeile_id, 0.0), z.score)
    treffer = [z.score for z in zu if z.zeile_id]
    return AlignmentErgebnis(zu, gedeckt, round(len(gedeckt) / m, 3) if m else 0.0,
                             round(sum(treffer) / len(treffer), 3) if treffer else 0.0, M)


def szenen_passung(saetze: list[str], szenen_zeilen: dict[str, list[SkriptZeileRef]]) -> dict[str, float]:
    """Welche Skriptszene passt am besten zu den gesprochenen Sätzen? (Fallback, wenn keine Klappe)."""
    out: dict[str, float] = {}
    for sz_id, zeilen in szenen_zeilen.items():
        if not zeilen or not saetze:
            out[sz_id] = 0.0
            continue
        M = aehnlichkeit_matrix(saetze, zeilen)
        beste = [max(row) for row in M]
        beste.sort(reverse=True)
        top = beste[: max(1, len(beste) // 2)]
        out[sz_id] = round(sum(top) / len(top), 3)
    return out
