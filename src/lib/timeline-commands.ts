/**
 * timeline-commands.ts — API commande unifiée pour la timeline.
 *
 * Contrat entre les 2 sources d'édition (humain via UI, agent IA via ReAct).
 * Chaque `TimelineCmd` est une action atomique sérialisable en JSON. Une
 * `Proposal` = une liste de cmds + un label → devient UN seul undo step quand
 * elle est acceptée.
 *
 * Golden rule : les commandes sont PURES sur le plan des données (elles
 * décrivent une intention, pas un impact DOM). L'application concrète se fait
 * dans Editor.tsx via un `executeCommand` qui traduit chaque cmd en mutation
 * du modèle timeline (setTlClips, snapshot pour undo…).
 *
 * L'agent IA n'a JAMAIS accès direct au DOM ni au state React. Il ne peut que
 * produire des `Proposal` — l'humain les accepte/refuse via l'UI.
 */

// ─── Commandes atomiques ─────────────────────────────────────────────────────

/**
 * Split : crée un cut à l'instant `at` sur tous les clips traversés (ou uniquement
 * sur `clipTlIds` si fourni). Un split ne supprime rien — il transforme 1 clip
 * en 2 clips adjacents.
 */
export type SplitCmd = {
  type: "split";
  at: number;              // instant timeline (secondes)
  clipTlIds?: string[];    // optionnel : cible spécifique ; sinon = tous les clips traversés
};

/**
 * Delete : supprime des clips par leur tlId. `ripple = true` colle la suite
 * par piste (V et A) ; `false` laisse un trou.
 */
export type DeleteCmd = {
  type: "delete";
  tlIds: string[];
  ripple: boolean;
};

/**
 * DeleteRange : supprime une PORTION temporelle sur les clips qui la traversent.
 * Concrètement = split at `from` + split at `to` + delete le segment [from, to]
 * sur chaque clip cible. Atomique : les IDs intermédiaires n'ont pas besoin
 * d'être connus par le producteur (agent IA).
 *
 * Sans `tlIds` : s'applique à tous les clips traversés (toutes pistes).
 * Avec `tlIds` : uniquement les clips listés.
 *
 * Use case principal : cleanup silences détectés par le backend.
 */
export type DeleteRangeCmd = {
  type: "deleteRange";
  from: number;         // secondes timeline
  to: number;           // secondes timeline
  ripple: boolean;
  tlIds?: string[];
};

/**
 * Move : déplace un clip à une nouvelle position (start absolu) sur sa piste
 * courante, ou change de piste si `videoTrackIndex` / `audioTrackIndex` fourni.
 */
export type MoveCmd = {
  type: "move";
  tlId: string;
  newStart: number;        // secondes
  newVideoTrackIndex?: number;
  newAudioTrackIndex?: number;
};

/**
 * Trim : ajuste le bord gauche ou droit d'un clip. Modifie `start` +
 * `mediaStart` + `duration` (left) ou juste `duration` (right).
 */
export type TrimCmd = {
  type: "trim";
  tlId: string;
  side: "left" | "right";
  delta: number;           // en secondes, positif = allonge, négatif = raccourcit
};

/**
 * Insert : place un clip source (par clipId) sur la timeline à `at`, sur la
 * piste indiquée. `mode` gère insertion vs overwrite :
 *   - "append" : simple ajout, pas de reflow
 *   - "insert" : décale la suite pour faire de la place (ripple insert)
 *   - "overwrite" : écrase ce qui était à cette position
 */
export type InsertCmd = {
  type: "insert";
  clipId: string;          // clipId source (bibliothèque média)
  at: number;              // secondes timeline
  videoTrackIndex: number;
  mode: "append" | "insert" | "overwrite";
  duration?: number;       // secondes ; défaut = toute la durée source
  mediaStart?: number;     // secondes ; défaut = 0
};

/** Set fade in ou out (durée + courbe optionnelle). */
export type SetFadeCmd = {
  type: "setFade";
  tlId: string;
  side: "in" | "out";
  duration: number;        // secondes (0 = pas de fade)
  curve?: number;          // -1 à +1 ; défaut 0
};

/** Set clip gain (rubber band). */
export type SetGainCmd = {
  type: "setGain";
  tlId: string;
  gainDb: number;          // dB ; 0 = unity
};

/** Set marker à un instant t. */
export type AddMarkerCmd = {
  type: "addMarker";
  at: number;
  label: string;
};

/** Set In/Out range. `null` = clear. */
export type SetRangeCmd = {
  type: "setRange";
  inPoint: number | null;
  outPoint: number | null;
};

/**
 * Charge une séquence entière de segments source sur la timeline (résultat
 * d'une génération IA : generate_story / generate_timeline_from_prompt). Chaque
 * item référence un clip média source + son in-point + sa durée. Les segments
 * sont posés séquentiellement (back-to-back) sur la piste vidéo 0.
 *   - replace=true  : remplace toute la timeline (premier montage)
 *   - replace=false : ajoute à la suite du contenu existant
 */
export type LoadSequenceCmd = {
  type: "loadSequence";
  segments: Array<{
    clipId: string;
    mediaStart: number;
    duration: number;
    name?: string;
  }>;
  replace: boolean;
};

/**
 * Union discriminée exhaustive. Ajouter un nouveau type ici oblige TS à
 * vérifier tous les switch/case qui consomment `TimelineCmd`.
 */
export type TimelineCmd =
  | SplitCmd
  | DeleteCmd
  | DeleteRangeCmd
  | MoveCmd
  | TrimCmd
  | InsertCmd
  | LoadSequenceCmd
  | SetFadeCmd
  | SetGainCmd
  | AddMarkerCmd
  | SetRangeCmd;

// ─── Proposals ───────────────────────────────────────────────────────────────

/**
 * Une Proposal est un batch de cmds + son contexte. Créée par l'agent IA ou
 * par un pipeline déterministe (silence detector). Le monteur l'accepte
 * globalement (1 undo step) ou item par item.
 */
export type Proposal = {
  id: string;
  title: string;                 // "Silences entfernen" ; affiché dans l'UI
  summary?: string;              // ligne descriptive : "12 Cuts, 8.4s réduits"
  createdBy: "agent" | "user" | "deterministic";
  createdAt: number;             // Date.now() côté server ou passé en args
  edits: TimelineCmd[];
  status: "pending" | "accepted" | "rejected" | "partial";
  /** Pour la traçabilité de la Bachelorarbeit : quel outil / prompt a produit
   *  cette proposal ; sert aux métriques d'acceptation. */
  provenance?: {
    tool: string;                // "detect_silences" | "find_fillers" | ...
    params?: Record<string, unknown>;
    agentThought?: string;       // trace ReAct du raisonnement
  };
};

// ─── Descriptions humaines ───────────────────────────────────────────────────

/**
 * Génère une string lisible pour l'UI et les logs. Utilisé par le panneau
 * de proposals et par les toasts après acceptation.
 */
export function describeCommand(cmd: TimelineCmd): string {
  switch (cmd.type) {
    case "split":
      return `Cut bei ${cmd.at.toFixed(2)}s`;
    case "delete":
      return `${cmd.tlIds.length} Clip${cmd.tlIds.length > 1 ? "s" : ""} ${cmd.ripple ? "rippled entfernen" : "entfernen"}`;
    case "deleteRange":
      return `Bereich ${cmd.from.toFixed(2)}s→${cmd.to.toFixed(2)}s ${cmd.ripple ? "rippled entfernen" : "entfernen"}`;
    case "move":
      return `Clip verschieben nach ${cmd.newStart.toFixed(2)}s`;
    case "trim":
      return `Clip ${cmd.side === "left" ? "links" : "rechts"} trimmen (${cmd.delta > 0 ? "+" : ""}${cmd.delta.toFixed(2)}s)`;
    case "insert":
      return `Clip einfügen bei ${cmd.at.toFixed(2)}s (${cmd.mode})`;
    case "loadSequence": {
      const total = cmd.segments.reduce((a, s) => a + s.duration, 0);
      return `${cmd.replace ? "Timeline generieren" : "Sequenz anhängen"}: ${cmd.segments.length} Segmente · ${total.toFixed(1)}s`;
    }
    case "setFade":
      return `Fade ${cmd.side === "in" ? "in" : "out"}: ${cmd.duration.toFixed(2)}s`;
    case "setGain":
      return `Gain: ${cmd.gainDb > 0 ? "+" : ""}${cmd.gainDb.toFixed(1)} dB`;
    case "addMarker":
      return `Marker "${cmd.label}" bei ${cmd.at.toFixed(2)}s`;
    case "setRange":
      return cmd.inPoint === null && cmd.outPoint === null
        ? "Range löschen"
        : `Range: ${cmd.inPoint?.toFixed(2) ?? "—"} → ${cmd.outPoint?.toFixed(2) ?? "—"}`;
  }
}

/** Résumé compact d'une Proposal pour l'entête du panneau. */
export function describeBatch(proposal: Proposal): string {
  const n = proposal.edits.length;
  return `${proposal.title} · ${n} Aktion${n > 1 ? "en" : ""}`;
}

// ─── Contract d'exécution ────────────────────────────────────────────────────

/**
 * Interface implémentée par Editor.tsx et exposée via useTimelineCommandsHost.
 * L'agent (ou tout autre consommateur) peut appeler ces méthodes pour appliquer
 * des commandes. `executeBatch` garantit UN seul snapshot (undo unifié).
 */
export interface TimelineCommandExecutor {
  /** Applique une commande unique. Fait son propre snapshot pour undo. */
  execute: (cmd: TimelineCmd) => void;
  /** Applique un batch avec UN seul snapshot pour undo. */
  executeBatch: (batch: TimelineCmd[], label: string) => void;
  /** Vérifie qu'une cmd est applicable dans l'état courant (clip existe,
   *  bornes valides…). Retourne { ok: true } ou { ok: false, reason }. */
  canExecute: (cmd: TimelineCmd) => { ok: true } | { ok: false; reason: string };
  /** Snapshot du state timeline courant — envoyé au backend agent pour qu'il
   *  raisonne sur ce qui est actuellement sur la timeline utilisateur. */
  getSnapshot: () => TimelineSnapshot;
}

/**
 * Vue sérialisable du state timeline pour envoi au backend agent. Ne contient
 * PAS les URLs des médias (le backend a déjà les clips en DB via `clip_id`).
 */
export interface TimelineSnapshot {
  totalDuration: number;
  fps: number;
  numVideoTracks: number;
  numAudioTracks: number;
  /** Instant courant du playhead (secondes). Sert à résoudre "aktueller Clip". */
  playheadTime: number;
  /** tlIds actuellement sélectionnés (multi-select possible). */
  selectedTlIds: string[];
  clips: Array<{
    tlId: string;
    clipId: string;
    name?: string;
    start: number;
    duration: number;
    mediaStart: number;
    videoTrackIndex?: number;
    audioTrackIndex?: number;
    hasAudio?: boolean;
  }>;
}
