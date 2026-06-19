# CinAssist — Soutenance Bachelor · Script

> À lire la veille et le matin. Pas à mémoriser mot-à-mot — mémoriser la **structure**.
> Date : 2026-05-22 · 14h50

---

## 1. Cadrage en 60 secondes (à dire en ouverture)

> *"CinAssist ist **kein Adobe-Konkurrent**, sondern ein **Forschungsprototyp für prompt-getriebene Szenenauswahl in Multicam-Material**. Der wissenschaftliche Beitrag ist die **multimodale Analyse-Pipeline** und die **zero-shot Retrieval-Methodik**, nicht eine fertige Schnitt-Software. Alles läuft 100 % lokal, ist reproduzierbar, und jede einzelne Designentscheidung ist auditierbar — Klick auf das `▣ Methodik`-Panel zeigt alle Phasen mit den Referenzen."*

→ Ce cadrage **désamorce** la critique "vibe-coded". Tu poses toi-même le contexte : prototype de recherche, pas produit.

---

## 2. La pipeline en 5 phases (le cœur)

Si le prof demande "comment ça marche ?", suis cet ordre. Chaque phase = 30-60 secondes.

### Phase 1 · Ingestion
- ffprobe → métadonnées (résolution, bitrate, codec)
- Proxy H.264 avec `-g 12` (keyframes fréquents) → seek précis dans le navigateur
- showwavespic → waveform PNG visible sur la timeline
- Thumbnail strip pour navigation rapide

### Phase 2 · Analyse multimodale (**le cœur**)
- **PySceneDetect** (Castellano, 2014-2024) → bornes de scènes via différence HSV, seuil 27 (empirique, des auteurs)
- **Whisper large-v3** (Radford et al., 2022) → transcription locale, langue auto-détectée
- **LLaVA-7B** (Liu et al., NeurIPS 2023) → description **factuelle** du thumbnail. Remplace LLaMA3 parce que LLaMA3 hallucinait des récits dramatiques pour du matériel musical
- **CLIP ViT-B/32** (Radford et al., ICML 2021) → embeddings 512-dim par scène, dans l'espace texte-image partagé

### Phase 3 · Sélection prompt-driven
- L'utilisateur (ou le chat-agent) écrit un prompt en langage naturel
- CLIP-Text-Encoder transforme ce prompt en vecteur 512-dim
- `cos(prompt, scène) = (E_t · E_s) / (‖E_t‖ · ‖E_s‖)` calculé pour chaque scène
- Top-K sélectionné, **trié temporellement**, déterministe (aucun sampling LLM)
- Visualisable via `◉ Atlas` — démontre que le système comprend l'espace sémantique

### Phase 4 · Beat-Sync (optionnel, ♪)
- librosa.beat.beat_track (Ellis, JNMR 2007) → tempo + temps des battements
- Bornes de coupes snappées au prochain battement ≥ position cible
- Mode rhythm-aware POUR musique, dés-activable POUR documentaire/interview

### Phase 5 · Évaluation quantitative
Quatre métriques calculées sur chaque sortie :
- **Diversität** = |sources uniques| / |segments| → mesure de variété de cadres
- **Wechselrate** = coupes / secondes → tempo du montage
- **Dialog-Treue** = mots conservés / mots totaux → fidélité au dialogue
- **Prompt-Relevanz** = moyenne cos(prompt, scènes choisies) → précision de la sélection

---

## 3. Démonstration en direct (le canevas)

> **Toujours commence par les 3 clips BYAM déjà analysés. Ne re-uploade rien en live — risque trop élevé.**

1. **Montre les Medien-Cards** → "Analyse ✓" + tooltip listant les modèles
2. **Onglet Assistent** → conversation : décris une intention → le bot propose 3 directions A/B/C + un Vorschlag
3. **Clic sur "Auf Timeline anwenden"** → timeline se remplit
4. **Sélectionne un segment** → le panneau "Warum dieses Segment?" apparaît (bas-droite). Pointe la barre de **CLIP-Prompt-Relevanz** : *"jede Auswahl ist quantitativ begründbar"*
5. **◉ Atlas** → modal s'ouvre. Tape un nouveau prompt → projection apparaît. Pointe les Top-K qui s'illuminent
6. **▣ Methodik** → ouvre le modal de référence. Lis la phase 2 à voix haute si demandé
7. **♪ Beat-Sync** → toggle ON → re-clique "Auf Timeline anwenden" → cuts s'alignent au beat

---

## 4. Questions hostiles anticipées (et tes réponses)

### Q : "C'est juste un wrapper sur des modèles existants. Où est ta contribution ?"
> *"Die Modelle sind Werkzeuge. Der wissenschaftliche Beitrag ist die **Kombination** und die **Methodik**: (1) faktische LLaVA-Beschreibungen statt LLaMA3-Halluzinationen — ein bewusster Tausch nach Identifikation des Problems; (2) prompt-getriebene Top-K-Retrieval als reproduzierbarer Ersatz für LLM-basierte Auswahl; (3) vier quantitative Metriken zur Selbstbewertung. Jede dieser Entscheidungen ist im Methodik-Panel dokumentiert."*

### Q : "Pourquoi PySceneDetect au lieu d'une approche end-to-end neuronale ?"
> *"PySceneDetect ist **deterministisch und überprüfbar**. HSV-Differenz mit Threshold 27 ist von den Autoren empirisch validiert. Eine neuronale Variante (z.B. TransNetV2) wäre eine Genauigkeitsverbesserung, aber würde Reproduzierbarkeit kosten und ist nicht das Bottleneck."*

### Q : "Les coupes ont l'air aléatoires pour la musique"
> *"Genau dafür ist der Beat-Sync-Toggle da. Ohne ihn ist das System content-aware aber rhythm-blind — was für Dokumentation oder Interview korrekt ist, aber für Musik nicht. Mit dem Toggle synchronisiert librosa die Schnittgrenzen auf den Beat. Das ist **eine Designentscheidung mit Toggle, nicht eine Schwäche**."*

### Q : "Pourquoi CLIP et pas un modèle plus récent comme BLIP-2 ou SigLIP ?"
> *"CLIP ViT-B/32 ist (a) klein genug für lokale Inferenz auf Apple Silicon, (b) der Standard in der Forschung — Vergleichbarkeit. BLIP-2 wäre besser in der Bild-Text-Ausrichtung, aber langsamer und größer. Trade-off war hier Lokalität vs. SOTA-Genauigkeit, dokumentiert."*

### Q : "Comment évaluez-vous la qualité d'un cut ?"
> *"Quantitativ via vier Metriken — Diversität, Wechselrate, Dialog-Treue, Prompt-Relevanz. Eine formale User-Study mit n>20 Editoren wäre der nächste Schritt, war im Bachelor-Zeitrahmen nicht machbar. Die Metriken sind aber **objektiv und reproduzierbar**, das ist wichtiger als subjektive Bewertung."*

### Q : "Et si je te donne un footage complètement différent (sport, interview, voyage) ?"
> *"Das System ist **genre-agnostisch** designed. Der Chat-Agent erkennt das Genre aus LLaVA-Beschreibungen und Whisper-Transkripten und passt seine Vorschläge an — drei strategische Richtungen statt fixer Stile. Wir können das jetzt testen, wenn Sie wollen — aber bedenken Sie: Analyse dauert ca. 2-3 Min pro Clip, das ist nicht real-time."*

### Q : "Y a-t-il un User-Test ?"
> *"Nein, das wäre außerhalb des Bachelor-Rahmens. Stattdessen quantitative Metriken + Reproduzierbarkeit. **Ich nenne das eine bewusste methodische Begrenzung**, kein Versäumnis."* — Cette réponse est cruciale : tu nommes la limite **avant** que le prof la nomme.

### Q : "Pourquoi avoir gardé LLaMA3 alors qu'il hallucinait ?"
> *"LLaMA3 ist Fallback für die LLM-Verfeinerung der Auswahl (optional und standardmäßig DEAKTIVIERT für Reproduzierbarkeit). Die Visualbeschreibung pro Szene macht LLaVA — das war der gezielte Tausch."*

---

## 5. Si quelque chose plante en direct

- **Si le chat ne répond pas** : "Das Backend muss neu starten — die Pipeline ist persistent in PostgreSQL, kein Datenverlust. Lassen Sie mich kurz neu starten." (Ctrl+C, relance)
- **Si Atlas vide** : "Mindestens 2 Szenen mit Embedding nötig — der Clip muss vollständig analysiert sein."
- **Si Beat-Sync produit des coupes bizarres** : Désactive le toggle. *"Beat-Sync ist für rhythmisches Material gedacht — bei Dialog deaktiviere ich es bewusst."*

---

## 6. Trois phrases à apprendre par cœur

1. *"Multimodale Pipeline mit vier Modellen: PySceneDetect, Whisper, LLaVA, CLIP — alle lokal, alle zitierbar."*
2. *"Prompt-getriebene Top-K-Selektion via cosine similarity in 512-dim CLIP-Raum, deterministisch und reproduzierbar."*
3. *"Vier quantitative Metriken zur Selbstbewertung — Diversität, Wechselrate, Dialog-Treue, Prompt-Relevanz."*

---

## 7. Ce que tu **NE** dois **PAS** dire

- ❌ "Ich habe nicht alle Modelle selbst trainiert" → évident, n'attire pas l'attention sur ce point
- ❌ "Es funktioniert nicht immer perfekt" → trop défensif. Dis "es ist ein Prototyp"
- ❌ "Ich weiß nicht" → remplace par "Das war außerhalb meines Scopes — der Bachelor sollte einen funktionierenden Prototyp mit dokumentierter Methodik liefern, keine vollständige Evaluations-Studie"
- ❌ Toute mention de "vibe-coding" — sauf si le prof l'évoque, alors tu réponds calmement par les références académiques

---

## 8. Avant la soutenance — checklist 14h00 le jour J

- [ ] Backend lancé : `uvicorn backend.main:app --port 8001` → vérifier que `Backend verbunden` est vert
- [ ] Celery worker lancé (pour ne pas avoir besoin de re-analyser)
- [ ] Frontend lancé : `npm run dev`
- [ ] Les 3 clips BYAM apparaissent avec **Analyse ✓**
- [ ] Test rapide : un cut avec Beat-Sync ON → vérifier qu'il finit sans crash
- [ ] Cmd+Shift+R une fois pour vider le cache
- [ ] Ouvre le Methodik-Panel une fois → confirme qu'il s'affiche bien
- [ ] Ouvre l'Atlas une fois → confirme qu'il charge

---

Tu peux le faire. **La méthodologie est solide. La défense est dans le cadrage, pas dans la perfection technique.**
