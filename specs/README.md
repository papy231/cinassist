# CinAssist — Spezifikationen (Soll-Dokumentation)

> **Zweck dieses Ordners.** Dies ist die *Soll-Spezifikation* von CinAssist: Was das
> System leisten **soll**, mit welchen Schnittstellen, Datenmodellen und Algorithmen.
> Sie ist bewusst getrennt von der reinen Code-Dokumentation (`ARCHITECTURE.md`) und
> vom wissenschaftlichen Verteidigungsdokument (`DEFENSE.md`).

Dieser Ordner ist die Grundlage für das Gespräch mit dem Code-Betreuer. Jede Spezifikation
folgt demselben Muster: **Ziel → Anforderungen (mit IDs) → Schnittstelle/Design →
Abnahmekriterien → Verweis auf den Code**. Dadurch lässt sich jede Aussage direkt im
Quellcode nachweisen.

---

## Lesereihenfolge

| # | Dokument | Inhalt |
|---|----------|--------|
| 00 | [Produktvision & Scope](00_Produktvision_und_Scope.md) | Vision, Ziele, Abgrenzung, Glossar, Anwendungsfälle |
| 01 | [Funktionale Anforderungen](01_Funktionale_Anforderungen.md) | User Stories + Requirements `FR-*` mit Abnahmekriterien |
| 02 | [Nichtfunktionale Anforderungen](02_Nichtfunktionale_Anforderungen.md) | `NFR-*`: lokal, Performance, Datenschutz, Reproduzierbarkeit |
| 03 | [Systemarchitektur](03_Systemarchitektur.md) | Komponenten, Schichten, Laufzeit, Datenfluss |
| 04 | [Backend-Spezifikation](04_Backend_Spezifikation.md) | Module, 4-Phasen-Pipeline, Worker, Konfiguration |
| 05 | [API-Spezifikation](05_API_Spezifikation.md) | REST- + WebSocket-Verträge (alle Endpunkte) |
| 06 | [Datenmodell-Spezifikation](06_Datenmodell_Spezifikation.md) | DB-Schema, DTOs, Timeline-JSON |
| 07 | [KI-Schnitt-Spezifikation](07_KI_Schnitt_Spezifikation.md) | Der Kernalgorithmus (Energie, Rollen, Bogen, Beam Search, Metriken) |
| 08 | [Frontend-Spezifikation](08_Frontend_Spezifikation.md) | Seiten, Komponenten, Stores, Interaktionen |
| 09 | [Abnahme & Evaluierung](09_Abnahme_und_Evaluierung.md) | Test- und Evaluierungskriterien |

---

## Konventionen

- **Anforderungs-IDs**: `FR-x` (funktional), `NFR-x` (nichtfunktional), `API-x`, `DM-x`
  (Datenmodell), `AI-x` (KI-Schnitt). IDs sind stabil und werden zwischen Dokumenten
  referenziert.
- **MUSS / SOLL / KANN** im Sinne von RFC 2119 (MUSS = verbindlich, SOLL = empfohlen,
  KANN = optional).
- **Status-Marker** je Anforderung: `[implementiert]`, `[teilweise]`, `[geplant]`.
  So bleibt sichtbar, wo Soll und Ist auseinanderlaufen — das ist für die Verteidigung
  ehrlicher als zu behaupten, alles sei fertig.

## Bezug zu den übrigen Dokumenten

- `ARCHITECTURE.md` — beschreibt das **Ist** (was der Code tut). Diese Specs beschreiben das **Soll**.
- `DEFENSE.md` — **wissenschaftliche Begründung** jeder Designentscheidung (Literatur, Alternativen).
- `blueprint/PHASE_*.md` — narrative, phasenweise Erklärung der Pipeline (Lernmaterial).
- `specs/` — **verbindliche Spezifikation** mit nachweisbaren Anforderungen.

---

*CinAssist — Bachelorarbeit „KI-gestützter Videoschnitt". Spezifikationsstand: Soll-Definition v1.*
