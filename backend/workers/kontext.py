"""Celery-Tasks der Kontext-Schicht (langlaufend: Übersetzung, LLM-Zusammenfassungen).

  cinassist.skript_import      — Datei parsen → Skript/Szenen/Zeilen (+ Übersetzung der Dialogzeilen)
  cinassist.kontext_aufbauen   — L2 (alle Takes) → L3 (alle Szenen, LLM) → L4 (Story, LLM)
  cinassist.schnittplan        — L5 Rohschnitt erzeugen (regelbasiert, schnell)
Fortschritt über `_update_job` (Job-Tabelle + Redis-Pub/Sub wie die Ingestion).
"""
from __future__ import annotations

import logging
from typing import Any

from backend.core.celery_app import celery_app
from backend.core.database import SyncSessionLocal, Skript
from backend.workers.ingest import _update_job

logger = logging.getLogger("cinassist.workers.kontext")


@celery_app.task(bind=True, name="cinassist.skript_import", max_retries=0)
def skript_import_task(self, pfad: str, name: str, ziel_sprache: str, job_id: str) -> dict[str, Any]:
    from backend.core.skript import kontext as KX
    db = SyncSessionLocal()
    try:
        _update_job(job_id, "laeuft", 5, "Drehbuch wird gelesen…", schritt="skript")
        sk = KX.importiere_skript(db, pfad, name, ziel_sprache, uebersetzen=False)
        _update_job(job_id, "laeuft", 30, f"{len(sk.szenen)} Szenen erkannt — Dialogzeilen werden übersetzt…", schritt="skript",
                    schritt_daten={"szenen": len(sk.szenen), "titel": sk.titel, "sprache": sk.sprache})
        n = KX.uebersetze_skript(db, sk) if sk.sprache != (sk.ziel_sprache or "de") else 0
        _update_job(job_id, "fertig", 100, f"Drehbuch importiert: {len(sk.szenen)} Szenen, {n} Zeilen übersetzt.",
                    {"skript_id": str(sk.id), "szenen": len(sk.szenen), "uebersetzt": n})
        return {"skript_id": str(sk.id)}
    except Exception as e:  # noqa: BLE001
        logger.exception("Skript-Import fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Skript-Import fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, name="cinassist.kontext_aufbauen", max_retries=0)
def kontext_aufbauen_task(self, skript_id: str, job_id: str, mit_llm: bool = True) -> dict[str, Any]:
    from backend.core.skript import kontext as KX
    db = SyncSessionLocal()
    try:
        sk = db.query(Skript).filter(Skript.id == skript_id).first()
        if not sk:
            _update_job(job_id, "fehler", 0, "Skript nicht gefunden.")
            return {"error": "skript"}
        _update_job(job_id, "laeuft", 5, "L2 — Klappen, Spiel/Produktion, Skript-Zuordnung je Take…", schritt="take_kontext")
        tks = KX.baue_take_kontexte(db, sk)
        zugeordnet = sum(1 for t in tks if t.skript_szene_id)
        _update_job(job_id, "laeuft", 30, f"L2 fertig: {zugeordnet}/{len(tks)} Takes einer Skriptszene zugeordnet. L3 — Szenen-Kontext (LLM)…",
                    schritt="take_kontext", schritt_daten={"takes": len(tks), "zugeordnet": zugeordnet})
        # Beats (Szenen-Takt): Skript → Beats, Take → monotone Beat-Segmentierung (Basis des Schnittplans)
        from backend.core.skript.beats import berechne_takt
        _update_job(job_id, "laeuft", 32, "Beats: Szenen-Takt je Take (Anker, Improvisation, Bild-Belege)…", schritt="take_kontext")
        berechne_takt(db, sk, nur_fehlende=False)
        n = len(sk.szenen)
        # L3 pro Szene mit Fortschritt
        from backend.core.skript.kontext import baue_szenen_kontexte
        ctxs = baue_szenen_kontexte(db, sk, mit_llm=mit_llm)
        _update_job(job_id, "laeuft", 85, f"L3 fertig: {len(ctxs)} Szenen. L4 — Story-Kontext…", schritt="szenen_kontext")
        KX.baue_story_kontext(db, sk, mit_llm=mit_llm)
        _update_job(job_id, "fertig", 100, "Kontext aufgebaut (Takes → Szenen → Story).",
                    {"takes": len(tks), "zugeordnet": zugeordnet, "szenen": len(ctxs)})
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        logger.exception("Kontext-Aufbau fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Kontext-Aufbau fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, name="cinassist.schnittplan", max_retries=0)
def schnittplan_task(self, skript_id: str, job_id: str, name: str | None = None, parameter: dict | None = None) -> dict[str, Any]:
    from backend.core.skript.schnittplan import erzeuge_schnittplan
    db = SyncSessionLocal()
    try:
        sk = db.query(Skript).filter(Skript.id == skript_id).first()
        if not sk:
            _update_job(job_id, "fehler", 0, "Skript nicht gefunden.")
            return {"error": "skript"}
        _update_job(job_id, "laeuft", 5, "Beats je Take werden geprüft/ergänzt…", schritt="schnittplan")
        try:
            from backend.core.skript.beats import berechne_takt
            berechne_takt(db, sk, nur_fehlende=not (parameter or {}).get("takt_neu", False))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Takt-Berechnung übersprungen: {e}")
        _update_job(job_id, "laeuft", 10, "Rohschnitt wird zusammengestellt…", schritt="schnittplan")
        plan = erzeuge_schnittplan(db, sk, name, parameter)
        # Gleich als Timeline persistieren (stil „rohschnitt“): der Editor stellt beim Start den neuesten Rohschnitt her —
        # der Nutzer muss nicht erst in den Skript-Tab.
        try:
            import uuid as _uuid
            from backend.core.database import Timeline, Clip as _Clip
            clips = {str(c.id): c for c in db.query(_Clip).all()}
            cursor = 0.0
            segs = []
            for e in plan.eintraege or []:
                c = clips.get(e["clip_id"])
                d = float(e["out_s"]) - float(e["in_s"])
                if not c or d <= 0:
                    continue
                start = float(e["tl_start"]) if e.get("tl_start") is not None else cursor
                segs.append({"id": f"{e['clip_id']}-plan-{plan.id.hex[:6]}-{e['nr']}", "clip_id": e["clip_id"],
                             "label": f"Sz{e['szene']} {e.get('einstellung') or ''} T{e.get('take') or '?'}"
                                      + (" · Alternative" if e.get("art") == "alternative" else " · Cutaway" if e.get("video_only") else " · Ton-Brücke" if e.get("audio_only") else ""),
                             "track": f"v{int(e.get('spur') or 1)}", "start": round(start, 3), "dauer": round(d, 3), "quelle": "A",
                             "media_start": float(e["in_s"]), "source_duration": c.dauer,
                             "video_only": bool(e.get("video_only")), "audio_only": bool(e.get("audio_only")),
                             "alternative": e.get("art") == "alternative",
                             "fade_in": float(e.get("fade_in") or 0), "fade_out": float(e.get("fade_out") or 0)})
                if not e.get("audio_only") and int(e.get("spur") or 1) == 1:
                    cursor = start + d
            db.add(Timeline(id=_uuid.uuid4(), name=plan.name, stil="rohschnitt", prompt=f"schnittplan:{plan.id}",
                            daten={"segmente": segs, "gesamtdauer": round(cursor, 3), "schnittplan_id": str(plan.id)}, gesamtdauer=round(cursor, 3)))
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Timeline-Persistenz des Schnittplans fehlgeschlagen: {e}")
        _update_job(job_id, "fertig", 100, f"Rohschnitt: {plan.statistik.get('eintraege')} Segmente, {plan.statistik.get('dauer_s')} s.",
                    {"plan_id": str(plan.id), **(plan.statistik or {})})
        return {"plan_id": str(plan.id)}
    except Exception as e:  # noqa: BLE001
        logger.exception("Schnittplan fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Schnittplan fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, name="cinassist.aktionen_pruefen", max_retries=0)
def aktionen_pruefen_task(self, skript_id: str, job_id: str, szenen: list[str] | None = None, neu_fragen: bool = False) -> dict[str, Any]:
    """Skript-gesteuerte Bildprüfung je Szene: Fragen aus den Aktionszeilen → VQA auf dichten Frames aller Takes der Szene →
    Aktions-Zeitfenster je Take + Coverage je Szene (gedreht/unsicher/fehlt)."""
    from backend.core.database import Clip, SkriptSzene, TakeKontext, SzenenKontext
    from backend.core.skript.aktionen import fragen_fuer_szene, pruefe_take, bewerte_take, frage_gewichte, aktions_coverage
    db = SyncSessionLocal()
    try:
        sk = db.query(Skript).filter(Skript.id == skript_id).first()
        if not sk:
            _update_job(job_id, "fehler", 0, "Skript nicht gefunden.")
            return {"error": "skript"}
        ziel = [sz for sz in sk.szenen if not szenen or sz.nummer in szenen]
        clips = {c.id: c for c in db.query(Clip).all()}
        gesamt_takes = sum(db.query(TakeKontext).filter(TakeKontext.skript_szene_id == sz.id).count() for sz in ziel) or 1
        erledigt = 0
        for sz in ziel:
            _update_job(job_id, "laeuft", int(95 * erledigt / gesamt_takes), f"Szene {sz.nummer}: Fragen aus dem Skript…", schritt="aktionen")
            fragen = fragen_fuer_szene(db, sz, neu=neu_fragen)
            tks = db.query(TakeKontext).filter(TakeKontext.skript_szene_id == sz.id).all()
            for tk in tks:
                c = clips.get(tk.clip_id)
                if c is None:
                    erledigt += 1; continue
                def prog(p, _e=erledigt, _n=c.dateiname, _s=sz.nummer):
                    _update_job(job_id, "laeuft", int(95 * (_e + p) / gesamt_takes), f"Szene {_s}: {_n} — Bildprüfung {int(p*100)} %", schritt="aktionen")
                if fragen:
                    pruefe_take(db, tk, c, fragen, fortschritt=prog)
                erledigt += 1
            # Phase 2: szenenweite Gewichte (Ja-Rate je Frage) → Spans je Take
            if fragen:
                gew = frage_gewichte([clips[t.clip_id] for t in tks if t.clip_id in clips], fragen)
                for tk in tks:
                    c = clips.get(tk.clip_id)
                    if c is not None:
                        bewerte_take(db, tk, c, fragen, gew)
            ctx = db.query(SzenenKontext).filter(SzenenKontext.skript_szene_id == sz.id).first()
            if ctx is not None:
                ctx.aktions_coverage = aktions_coverage(sz, tks, clips)
                db.commit()
        # Bild-Belege sind Beat-Evidenz → Takt der geprüften Szenen neu rechnen
        try:
            from backend.core.skript.beats import berechne_takt
            berechne_takt(db, sk, nur_fehlende=False)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Takt nach Bildprüfung nicht aktualisiert: {e}")
        _update_job(job_id, "fertig", 100, f"Bildprüfung fertig: {len(ziel)} Szenen, {erledigt} Takes.", {"szenen": [s.nummer for s in ziel], "takes": erledigt})
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        logger.exception("Aktionen-Prüfung fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Bildprüfung fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, name="cinassist.gesichter", max_retries=0)
def gesichter_task(self, skript_id: str, job_id: str) -> dict[str, Any]:
    """Gesichter → Personen-Cluster → Namen aus dem Skript → je Take „wer ist wann im Bild“."""
    from backend.core.skript.gesichter import laufe
    db = SyncSessionLocal()
    try:
        sk = db.query(Skript).filter(Skript.id == skript_id).first()
        if not sk:
            _update_job(job_id, "fehler", 0, "Skript nicht gefunden.")
            return {"error": "skript"}
        def prog(p, msg):
            _update_job(job_id, "laeuft", int(95 * p), msg, schritt="gesichter")
        res = laufe(db, sk, fortschritt=prog)
        _update_job(job_id, "fertig", 100, f"Gesichter: {res['cluster']} Personen erkannt, {res['benannt']} per Skript benannt, {res['takes']} Takes.", res)
        return res
    except Exception as e:  # noqa: BLE001
        logger.exception("Gesichter fehlgeschlagen")
        _update_job(job_id, "fehler", 0, f"Gesichtserkennung fehlgeschlagen: {e}")
        return {"error": str(e)}
    finally:
        db.close()
