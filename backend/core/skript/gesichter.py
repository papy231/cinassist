"""Gesichter ↔ Figuren je Einstellung — für echte Reaktionsschnitte.

Ablauf (deterministisch, nur das Gesichts-Embedding ist ein Modell):
  1. je Clip Frames (alle 5 s, 896 px aus dem Proxy) → MTCNN-Gesichter (p ≥ 0,9) → FaceNet-Embedding (VGGFace2), Cache
     PROXY_DIR/gesichter/<clip>.json
  2. alle Gesichter des Korpus greedy clustern (Kosinus ≥ SCHWELLE gegen den Cluster-Schwerpunkt) → „Personen“
  3. Namen aus dem SKRIPT: Präsenz-Matrix Figur × Skript-Szene (Sprecher-Cues + Namen in Aktionszeilen) gegen die
     Präsenz-Matrix Cluster × Szene (Anteil der Takes mit diesem Gesicht) → beste Zuordnung (kleine Mengen, greedy
     nach Übereinstimmung). Film-Namen aus der Story-Figurenzuordnung (ORPHEUS = Ophelia …). Der Nutzer kann umbenennen.
  4. je Take: {cluster: spans, anteil} → „wer ist wann im Bild“ (TakeKontext.gesichter).
"""
from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import uuid
from collections import defaultdict
from pathlib import Path

from backend.core.config import FFMPEG_BIN, PROXY_DIR
from backend.core.database import Clip, Skript, TakeKontext, GesichtsCluster
from backend.core.medien import clip_stem

logger = logging.getLogger("cinassist.skript.gesichter")
G_DIR = PROXY_DIR / "gesichter"
G_DIR.mkdir(parents=True, exist_ok=True)
SCHRITT = 5.0
BREITE = 1920            # aus dem ORIGINAL (Totalen: Gesichter im 960-px-Proxy nur ~25 px → unbrauchbare Embeddings)
SCHWELLE = 0.50          # Kosinus gegen Cluster-Schwerpunkt (FaceNet, L2-normalisiert; gemessen: gleiche Person 0,5–0,8)
MIN_PROB = 0.95
MIN_BREITE = 40          # px Gesichtsbreite bei 1920

_mtcnn = None
_resnet = None


def _modelle():
    global _mtcnn, _resnet
    if _mtcnn is None:
        from facenet_pytorch import MTCNN, InceptionResnetV1
        _mtcnn = MTCNN(keep_all=True, device="cpu", min_face_size=24)
        _resnet = InceptionResnetV1(pretrained="vggface2").eval()
    return _mtcnn, _resnet


def _frames(quelle: Path, stem: str, dauer: float) -> list[tuple[float, Path]]:
    out = []
    tmp = G_DIR / "frames"
    tmp.mkdir(exist_ok=True)
    t = SCHRITT / 2
    while t < dauer:
        fp = tmp / f"{stem}_{int(t * 10):06d}.jpg"
        if not fp.exists():
            subprocess.run([FFMPEG_BIN, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(quelle), "-frames:v", "1", "-q:v", "3",
                            "-vf", f"scale={BREITE}:-2", str(fp)], capture_output=True, timeout=120)
        if fp.exists() and fp.stat().st_size > 0:
            out.append((round(t, 2), fp))
        t += SCHRITT
    return out


def erkenne_clip(clip: Clip, fortschritt=None) -> list[dict]:
    """Gesichter eines Clips (gecacht): [{t, box, p, emb(512)}]."""
    cache_p = G_DIR / f"{clip.id}.json"
    if cache_p.exists():
        try:
            return json.loads(cache_p.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    original = Path(clip.dateipfad) if clip.dateipfad else None
    proxy = PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    quelle = original if (original and original.exists()) else proxy
    if not (quelle.exists() and quelle.stat().st_size > 0):
        return []
    import torch
    from PIL import Image
    mtcnn, resnet = _modelle()
    frames = _frames(quelle, clip_stem(clip), float(clip.dauer or 0.0))
    out: list[dict] = []
    for i, (t, fp) in enumerate(frames):
        try:
            img = Image.open(fp).convert("RGB")
            boxes, probs = mtcnn.detect(img)
            if boxes is None:
                continue
            faces = mtcnn.extract(img, boxes, None)
            if faces is None:
                continue
            with torch.no_grad():
                embs = resnet(faces)
            for b, p, e in zip(boxes, probs, embs):
                if p is None or float(p) < MIN_PROB or (float(b[2]) - float(b[0])) < MIN_BREITE:
                    continue
                v = e.numpy().astype(float)
                n = float((v ** 2).sum() ** 0.5) or 1.0
                out.append({"t": t, "box": [round(float(x)) for x in b], "p": round(float(p), 3), "emb": [round(float(x) / n, 5) for x in v]})
        except Exception as ex:  # noqa: BLE001
            logger.warning(f"Gesichter {clip.dateiname} @{t}: {ex}")
        if fortschritt:
            fortschritt((i + 1) / max(1, len(frames)))
    cache_p.write_text(json.dumps(out), "utf-8")
    return out


def _cos(a, b) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def clustere(gesichter_je_clip: dict[str, list[dict]], schwelle: float = SCHWELLE) -> tuple[dict[str, list[tuple[int, dict]]], list[dict]]:
    """Greedy: jedes Gesicht zum Cluster mit höchster Schwerpunkt-Ähnlichkeit (≥ schwelle), sonst neuer Cluster.
    Liefert (clip → [(cluster_idx, gesicht)], cluster-liste [{idx, n, schwerpunkt, beispiel(clip,t,box)}])."""
    cluster: list[dict] = []
    zuord: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    # größere/sicherere Gesichter zuerst → stabile Schwerpunkte
    alle = [(cid, g) for cid, gs in gesichter_je_clip.items() for g in gs]
    alle.sort(key=lambda x: -(x[1]["p"] * (x[1]["box"][2] - x[1]["box"][0])))
    for cid, g in alle:
        best, best_s = None, 0.0
        for c in cluster:
            s_ = _cos(g["emb"], c["schwerpunkt"])
            if s_ > best_s:
                best, best_s = c, s_
        if best is not None and best_s >= schwelle:
            n = best["n"]
            best["schwerpunkt"] = [(a * n + b) / (n + 1) for a, b in zip(best["schwerpunkt"], g["emb"])]
            nn = math.sqrt(sum(x * x for x in best["schwerpunkt"])) or 1.0
            best["schwerpunkt"] = [x / nn for x in best["schwerpunkt"]]
            best["n"] = n + 1
            if g["p"] * (g["box"][2] - g["box"][0]) > best["beispiel_score"]:
                best["beispiel"] = (cid, g["t"], g["box"]); best["beispiel_score"] = g["p"] * (g["box"][2] - g["box"][0])
            zuord[cid].append((best["idx"], g))
        else:
            c = {"idx": len(cluster), "n": 1, "schwerpunkt": list(g["emb"]), "beispiel": (cid, g["t"], g["box"]),
                 "beispiel_score": g["p"] * (g["box"][2] - g["box"][0])}
            cluster.append(c)
            zuord[cid].append((c["idx"], g))
    return zuord, cluster


def figuren_praesenz_skript(sk: Skript) -> tuple[list[str], dict[str, set[str]]]:
    """Figur → Menge der Szenennummern, in denen sie laut Skript vorkommt (Sprecher-Cue ODER Name in Aktionszeilen)."""
    cues = {(z.figur or "").upper() for sz in sk.szenen for z in sz.zeilen if z.art == "dialog" and z.figur}
    namen = set(cues)
    # Namen, die nur angesprochen/beschrieben werden (EURYDICE): Großwörter, die in Regie-Klammern („to Eurydice“) auftauchen
    for sz in sk.szenen:
        for z in sz.zeilen:
            if z.regie:
                for w in re.findall(r"[A-Z][a-z]{3,}", z.regie):
                    namen.add(w.upper())
    praesenz: dict[str, set[str]] = defaultdict(set)
    for sz in sk.szenen:
        text = " ".join(z.text for z in sz.zeilen if z.art == "aktion") + " " + " ".join((z.regie or "") for z in sz.zeilen)
        for f in namen:
            if any((z.figur or "").upper() == f for z in sz.zeilen if z.art == "dialog"):
                praesenz[f].add(sz.nummer)
            elif re.search(r"\b" + f.capitalize() + r"\b", text) or re.search(r"\b" + f[:4].capitalize(), text):
                praesenz[f].add(sz.nummer)
    return sorted(namen), praesenz


def ordne_cluster_figuren(cluster: list[dict], cluster_szenen: dict[int, dict[str, float]], figuren: list[str],
                          praesenz: dict[str, set[str]], alle_szenen: list[str]) -> dict[int, tuple[str, float]]:
    """Cluster → (Figur, Übereinstimmung) per Präsenz-Abgleich: +Anteil in Szenen, wo die Figur laut Skript ist,
    −Anteil in Szenen, wo sie nicht ist. Greedy nach bestem Score, jede Figur höchstens einmal; nur Cluster mit
    Gesichtern in ≥ 2 Takes werden benannt."""
    scores = []
    for c in cluster:
        cs = cluster_szenen.get(c["idx"], {})
        if sum(1 for v in cs.values() if v > 0) == 0:
            continue
        for f in figuren:
            soll = praesenz.get(f, set())
            sc = sum(cs.get(sz, 0.0) * (1.0 if sz in soll else -1.0) for sz in alle_szenen)
            scores.append((sc, c["idx"], f))
    scores.sort(reverse=True)
    out: dict[int, tuple[str, float]] = {}
    vergeben: set[str] = set()
    for sc, idx, f in scores:
        if idx in out or f in vergeben or sc <= 0:
            continue
        out[idx] = (f, round(sc, 3)); vergeben.add(f)
    return out


def speichere_thumb(clip: Clip, t: float, box: list[int], ziel: Path) -> bool:
    original = Path(clip.dateipfad) if clip.dateipfad else None
    proxy = original if (original and original.exists()) else PROXY_DIR / f"{clip_stem(clip)}_proxy.mp4"
    if not proxy.exists():
        return False
    x0, y0, x1, y1 = box
    w, h = max(16, x1 - x0), max(16, y1 - y0)
    m = int(max(w, h) * 0.35)
    subprocess.run([FFMPEG_BIN, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(proxy), "-frames:v", "1",
                    "-vf", f"scale={BREITE}:-2,crop={w + 2*m}:{h + 2*m}:{max(0, x0 - m)}:{max(0, y0 - m)},scale=160:-2", str(ziel)],
                   capture_output=True, timeout=60)
    return ziel.exists() and ziel.stat().st_size > 0


def verschmelze(cluster: list[dict], zuord: dict[str, list[tuple[int, dict]]], schwelle: float = 0.60) -> tuple[list[dict], dict[str, list[tuple[int, dict]]]]:
    """Zweiter Pass: Cluster, deren Schwerpunkte sich ≥ schwelle ähneln, zusammenlegen (Pose/Licht zersplittern
    dieselbe Person in der Greedy-Phase — gemessen c0~c2 = 0,73 bei derselben Darstellerin)."""
    cluster = sorted(cluster, key=lambda c: -c["n"])
    alias: dict[int, int] = {}
    behalten: list[dict] = []
    for c in cluster:
        ziel = None
        for b in behalten:
            if _cos(c["schwerpunkt"], b["schwerpunkt"]) >= schwelle:
                ziel = b; break
        if ziel is None:
            behalten.append(c); alias[c["idx"]] = c["idx"]
        else:
            n1, n2 = ziel["n"], c["n"]
            ziel["schwerpunkt"] = [(a * n1 + b * n2) / (n1 + n2) for a, b in zip(ziel["schwerpunkt"], c["schwerpunkt"])]
            nn = math.sqrt(sum(x * x for x in ziel["schwerpunkt"])) or 1.0
            ziel["schwerpunkt"] = [x / nn for x in ziel["schwerpunkt"]]
            ziel["n"] = n1 + n2
            if c["beispiel_score"] > ziel["beispiel_score"]:
                ziel["beispiel"], ziel["beispiel_score"] = c["beispiel"], c["beispiel_score"]
            alias[c["idx"]] = ziel["idx"]
    neu_zuord = {cid: [(alias.get(i, i), g) for i, g in lst] for cid, lst in zuord.items()}
    return behalten, neu_zuord


def laufe(db, sk: Skript, fortschritt=None) -> dict:
    """Kompletter Lauf: Gesichter je Clip → Cluster → Namen aus dem Skript → Persistenz (GesichtsCluster + TakeKontext.gesichter)."""
    from backend.core.database import StoryKontext
    tks = db.query(TakeKontext).all()
    clips = {c.id: c for c in db.query(Clip).filter(Clip.status == "analysiert").all()}
    tk_by_clip = {t.clip_id: t for t in tks}
    szene_nr = {sz.id: sz.nummer for sz in sk.szenen}
    alle_szenen = [sz.nummer for sz in sk.szenen]
    # 1) erkennen
    G: dict[str, list[dict]] = {}
    n = len(clips)
    for i, (cid, c) in enumerate(clips.items()):
        G[str(cid)] = erkenne_clip(c)
        if fortschritt:
            fortschritt(0.8 * (i + 1) / max(1, n), f"Gesichter: {c.dateiname} ({i+1}/{n})")
    # 2) clustern + verschmelzen
    zuord, cluster = clustere(G)
    cluster, zuord = verschmelze(cluster, zuord)
    # 3) Präsenz je Szene: Anteil der Takes der Szene, in denen der Cluster ≥ 2 Gesichter hat
    takes_je_szene: dict[str, int] = defaultdict(int)
    cluster_take_szene: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for cid, c in clips.items():
        tk = tk_by_clip.get(cid)
        sz = szene_nr.get(tk.skript_szene_id) if tk and tk.skript_szene_id else None
        if not sz:
            continue
        takes_je_szene[sz] += 1
        zaehl: dict[int, int] = defaultdict(int)
        for idx, _ in zuord.get(str(cid), []):
            zaehl[idx] += 1
        for idx, k in zaehl.items():
            if k >= 2:
                cluster_take_szene[idx][sz].add(str(cid))
    cluster_szenen: dict[int, dict[str, float]] = {}
    for c in cluster:
        cluster_szenen[c["idx"]] = {sz: round(len(cluster_take_szene[c["idx"]].get(sz, set())) / max(1, takes_je_szene.get(sz, 1)), 3) for sz in alle_szenen}
    # 4) Namen aus dem Skript
    figuren, praesenz = figuren_praesenz_skript(sk)
    gross = [c for c in cluster if sum(len(v) for v in cluster_take_szene[c["idx"]].values()) >= 2]
    zuordnung = ordne_cluster_figuren(gross, cluster_szenen, figuren, praesenz, alle_szenen)
    # Unbenannte Cluster (Profil, Gegenlicht) an die ähnlichste benannte Person anhängen, wenn eindeutig
    # (Kosinus ≥ 0,45 und Abstand zum Zweitbesten ≥ 0,05) — gemessen: Profil derselben Darstellerin 0,53 zum frontalen Cluster.
    benannt = {c["idx"]: c for c in gross if c["idx"] in zuordnung}
    for c in gross:
        if c["idx"] in zuordnung or not benannt:
            continue
        sims = sorted(((_cos(c["schwerpunkt"], b["schwerpunkt"]), bi) for bi, b in benannt.items()), reverse=True)
        if sims and sims[0][0] >= 0.45 and (len(sims) == 1 or sims[0][0] - sims[1][0] >= 0.05):
            f, _ = zuordnung[sims[0][1]]
            zuordnung[c["idx"]] = (f, round(-sims[0][0], 3))   # negativer Score = „abgeleitet über Ähnlichkeit“
    story = db.query(StoryKontext).filter(StoryKontext.skript_id == sk.id).first()
    film_namen = {str(f.get("skript") or "").upper(): f.get("film") for f in (story.figuren if story and isinstance(story.figuren, list) else []) if isinstance(f, dict)}
    # 5) persistieren (alte Cluster des Skripts ersetzen, manuelle Namen per Skript-Name übernehmen)
    alt = {(c.name_skript or "").upper(): c for c in db.query(GesichtsCluster).filter(GesichtsCluster.skript_id == sk.id).all() if c.manuell}
    db.query(GesichtsCluster).filter(GesichtsCluster.skript_id == sk.id).delete(synchronize_session=False)
    db.commit()
    idx_to_id: dict[int, uuid.UUID] = {}
    for c in gross:
        name_skript, score = zuordnung.get(c["idx"], (None, None))
        gid = uuid.uuid4()
        thumb = G_DIR / f"cluster_{gid.hex[:8]}.jpg"
        bcid, bt, bbox = c["beispiel"]
        bclip = clips.get(uuid.UUID(bcid))
        ok = speichere_thumb(bclip, bt, bbox, thumb) if bclip else False
        manuell_alt = alt.get((name_skript or "").upper())
        row = GesichtsCluster(id=gid, skript_id=sk.id, idx=c["idx"], anzahl=c["n"],
                              takes=sum(len(v) for v in cluster_take_szene[c["idx"]].values()),
                              name_skript=name_skript, name_film=(manuell_alt.name_film if manuell_alt else film_namen.get((name_skript or "").upper()) or None),
                              score=score, manuell=bool(manuell_alt), thumb_pfad=(f"/proxies/gesichter/{thumb.name}" if ok else None),
                              szenen_anteil=cluster_szenen.get(c["idx"]))
        db.add(row)
        idx_to_id[c["idx"]] = gid
    db.commit()
    # 6) je Take: wer ist wann im Bild
    for cid, c in clips.items():
        tk = tk_by_clip.get(cid)
        if tk is None:
            continue
        frames_gesamt = len({g["t"] for g in G.get(str(cid), [])}) or 1
        per: dict[str, dict] = {}
        for idx, g in zuord.get(str(cid), []):
            gid = idx_to_id.get(idx)
            if gid is None:
                continue
            d = per.setdefault(str(gid), {"frames": 0, "ts": []})
            d["frames"] += 1; d["ts"].append(float(g["t"]))
        out: dict[str, dict] = {}
        for gid, d in per.items():
            ts = sorted(set(d["ts"]))
            spans: list[list[float]] = []
            for t in ts:
                if spans and t - spans[-1][1] <= SCHRITT * 1.5:
                    spans[-1][1] = t + SCHRITT / 2
                else:
                    spans.append([max(0.0, t - SCHRITT / 2), t + SCHRITT / 2])
            out[gid] = {"frames": d["frames"], "anteil": round(len(ts) / frames_gesamt, 3), "spans": [[round(a, 1), round(b, 1)] for a, b in spans]}
        tk.gesichter = out
    db.commit()
    if fortschritt:
        fortschritt(1.0, "Gesichter fertig")
    return {"cluster": len(gross), "benannt": sum(1 for c in gross if c["idx"] in zuordnung), "takes": len(clips)}
