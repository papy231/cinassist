"""Integrationsszenarien des KI-Agenten gegen die ECHTE DB + Ollama (run_sync) — manuell, nicht pytest.

Voraussetzungen: uvicorn auf :8001, Ollama mit qwen2.5:14b, Projekt „Pinky Promise" mit Kontext-Schicht + Plan.
Aufruf:  backend/.venv/bin/python backend/tests/szenarien_agent_live.py            # alle (S1–S6)
         backend/.venv/bin/python backend/tests/szenarien_agent_live.py 4 5       # Auswahl
Jedes Szenario asserted Tool-Wahl und (bei Bearbeitungen) die exakten validierten Kommandos. S7 (regenerate)
erzeugt einen echten Plan + Timeline — danach ggf. aufräumen.

Hinweis: In S2 und S10 ist die Frage bewusst französisch gestellt. Geprüft wird damit, ob der
Assistent die Sprache der Eingabe versteht und dennoch auf Deutsch antwortet."""
import json, os, sys, time, urllib.request

API = "http://localhost:8001/api/agent/run_sync"
ERGEBNISSE: list[dict] = []          # [{szenario, dauer_s, llm_calls, tools, ok}] — für Vergleichsläufe (--json <datei>)
SNAP = {"totalDuration": 445.5, "fps": 24, "numVideoTracks": 2, "numAudioTracks": 2, "playheadTime": 60.0,
        "selectedTlIds": ["tl-9"],
        "clips": [
            {"tlId": "tl-8", "clipId": "a8f4a562-37b4-4e84-811d-e553e0398633", "name": "Sz2 2.1 T2", "start": 24.5, "duration": 43.2, "mediaStart": 35.0, "videoTrackIndex": 0, "hasAudio": True},
            {"tlId": "tl-9", "clipId": "d99a1f28-0000-4000-8000-000000000000", "name": "Sz2 2.2 T4", "start": 67.7, "duration": 30.6, "mediaStart": 38.8, "videoTrackIndex": 0, "hasAudio": True},
            {"tlId": "tl-10", "clipId": "0a2d4ec4-166d-41cb-a53f-fbb0253228e0", "name": "Sz2 2.1 T4", "start": 98.3, "duration": 20.7, "mediaStart": 82.0, "videoTrackIndex": 0, "hasAudio": True},
        ]}

def lauf(name, prompt, timeline_state=None, history=None, timeout=420):
    t0 = time.time()
    body = json.dumps({"prompt": prompt, "timeline_state": timeline_state, "history": history}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    dauer = time.time() - t0
    tools = [e["name"] for e in d["trace"] if e["type"] == "action"]
    obs = [e["content"] for e in d["trace"] if e["type"] == "observation"]
    llm_calls = sum(1 for e in d["trace"] if e["type"] == "thought")
    tok = sum((e.get("meta") or {}).get("tokens") or 0 for e in d["trace"])
    print(f"\n===== {name} =====  [{dauer:.1f}s · {llm_calls} LLM-Calls · {tok} tok]")
    print("TOOLS:", tools)
    print("FINAL:", (d.get("final_answer") or "")[:400])
    ERGEBNISSE.append({"szenario": name, "dauer_s": round(dauer, 1), "llm_calls": llm_calls, "tokens": tok, "tools": tools})
    return tools, obs, d.get("final_answer") or ""

if __name__ == "__main__":
    argv = [a for i, a in enumerate(sys.argv[1:], 1) if a != "--json" and sys.argv[i - 1] != "--json"]
    welche = [a for a in argv if not a.startswith("-")] or ["1", "2", "3", "4", "5", "6", "9", "10"]
    if "1" in welche:
        t, o, f = lauf("S1 Warum-Frage", "Warum wurde in Szene 2 für Beat 3 die Einstellung 2.1 Take 4 gewählt?")
        assert any(x in t for x in ("get_plan", "get_scene_context")), "kein Wissens-Tool"
        assert not any(x in t for x in ("edit_timeline", "regenerate_schnittplan", "generate_story")), "Editier-Tool bei Frage!"
    if "2" in welche:
        t, o, f = lauf("S2 Lücken-Frage (frz.)", "Quelles lignes du scénario manquent dans le plan actuel et pourquoi ?")
        assert any(x in t for x in ("get_plan", "get_script_overview", "get_scene_context")), "kein Wissens-Tool"
    if "3" in welche:
        t, o, f = lauf("S3 Transkript-Suche", "Wo wird „Tee machen“ gesagt? Nenne Takes und Zeitpunkte.")
        assert "search_transcripts" in t, "search_transcripts nicht benutzt"
    if "4" in welche:
        t, o, f = lauf("S4 Clip kürzen", "Kürze den ausgewählten Clip um 2 Sekunden am Ende.", timeline_state=SNAP)
        assert "edit_timeline" in t, "edit_timeline nicht benutzt"
        cmds = next((x.get("commands") for x in o if isinstance(x, dict) and x.get("commands")), None)
        assert cmds and cmds[0]["type"] == "trim" and cmds[0]["tlId"] == "tl-9" and abs(cmds[0]["delta"] + 2) < 0.01, f"Kommandos falsch: {cmds}"
    if "5" in welche:
        hist = [{"role": "user", "content": "Kürze den ausgewählten Clip um 2 Sekunden am Ende."},
                {"role": "assistant", "content": "Vorschlag erstellt: 1 Trim (−2 s am Ende von Sz2 2.2 T4, tlId tl-9). Bitte im Editor akzeptieren."}]
        t, o, f = lauf("S5 Folgeauftrag (Historie)", "Mach lieber 3 Sekunden.", timeline_state=SNAP, history=hist)
        assert "edit_timeline" in t, "edit_timeline nicht benutzt (Historie nicht verstanden)"
        cmds = next((x.get("commands") for x in o if isinstance(x, dict) and x.get("commands")), None)
        assert cmds and cmds[0]["type"] == "trim" and abs(cmds[0]["delta"] + 3) < 0.01, f"Kommandos falsch: {cmds}"
    if "6" in welche:
        t, o, f = lauf("S6 Beat-Quelle tauschen", "Zeige in Szene 2 den Beat 3 aus Einstellung 2.1 Take 2 statt Take 4.")
        assert "swap_beat_source" in t, "swap_beat_source nicht benutzt"
        segs = next((x.get("segments") for x in o if isinstance(x, dict) and x.get("segments")), None)
        assert segs, "keine Segmente im Swap"

    if "9" in welche:
        # Interpretation impliziter Profi-Annahmen: chronologisch = Skript-Reihenfolge + Klappe raus + bester Take
        t, o, f = lauf("S9 Chronologisch (implizite Annahmen)", "Lege die Sequenzen chronologisch nach dem Skript auf die Timeline.")
        assert "lege_sequenzen_chronologisch" in t, f"falsches Tool: {t}"
        res = next((x for x in o if isinstance(x, dict) and x.get("segments")), None)
        assert res, "keine Segmente"
        assert all(s_["media_start"] >= 0.5 for s_ in res["segments"]), "Klappe/Einrichten nicht übersprungen?"
        assert len(res["segments"]) >= 20, f"nur {len(res['segments'])} Segmente — Einstellungen fehlen (eine pro Szene wäre falsch)"
        assert sum(1 for s_ in res["segments"] if "Sz2" in (s_.get("clip_name") or "")) >= 3, "Szene 2: 2.1/2.2/2.4 erwartet"
        assert sum(1 for s_ in res["segments"] if "Sz5" in (s_.get("clip_name") or "")) >= 3, "Szene 5: Teile/Einstellungen fehlen"
        assert all(s_["duration"] >= 1.0 for s_ in res["segments"]), "Mini-Segment < 1 s (Fenster-Bug?)"
        assert any(w in f.lower() for w in ("klappe", "annahme", "bester take", "beste take")), f"Annahmen nicht genannt: {f[:200]}"
    if "10" in welche:
        t, o, f = lauf("S10 Deutsch trotz frz. Frage", "Pourquoi le plan a choisi 2.1 Take 4 pour le beat 3 de la scène 2 ?")
        assert any(w in f for w in ("laut Plan", "Laut", "Szene", "Einstellung")), f"nicht deutsch? {f[:150]}"
    if "11" in welche:
        t, o, f = lauf("S11 Alternativen-Stapel", "Zeige mir Alternativen für Szene 2 auf V2.", timeline_state=SNAP)
        assert "lege_alternativen" in t, f"falsches Tool: {t}"
        cmds = next((x.get("commands") for x in o if isinstance(x, dict) and x.get("commands")), None)
        assert cmds and all(c["type"] == "insert" and c.get("videoOnly") and c["videoTrackIndex"] >= 1 for c in cmds), f"Kommandos falsch: {cmds and cmds[:2]}"
    print("\nALLE GEWÄHLTEN SZENARIEN OK")
    gesamt = sum(e["dauer_s"] for e in ERGEBNISSE)
    print(f"GESAMT: {gesamt:.0f}s über {len(ERGEBNISSE)} Szenarien · " + " · ".join(f"{e['szenario'].split()[0]}={e['dauer_s']}s" for e in ERGEBNISSE))
    ziel = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--json" and i + 1 < len(sys.argv)), None)
    if ziel:
        json.dump({"modell": os.environ.get("BENCH_LABEL", "?"), "ergebnisse": ERGEBNISSE}, open(ziel, "w"), ensure_ascii=False, indent=1)
        print("→", ziel)

def s7():
    t, o, f = lauf("S7 Regenerate", "Erzeuge einen neuen Feinschnitt.", timeout=600)
    assert "regenerate_schnittplan" in t, "regenerate nicht benutzt"
    res = next((x for x in o if isinstance(x, dict) and x.get("plan_id")), None)
    assert res and res.get("segments"), "kein Plan/Segmente"
    print("PLAN:", res["name"], res["eintraege"], "Segmente,", res["dauer_s"], "s")

