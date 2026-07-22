/**
 * style-prefs.ts — Store Zustand des préférences de style utilisateur.
 *
 * Ces préférences sont persistées en localStorage et injectées dans chaque
 * appel à l'agent via `timeline_state.style_prefs`. Le backend étend son
 * system prompt en fonction — l'agent adapte ses recommandations (durée cible
 * de rough cut, cadence des coupes, préférences framing, etc.).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Language = "de" | "en" | "fr";
export type CuttingStyle = "fast" | "moderate" | "slow";

export interface FramingMix {
  closeup: number; // 0-100 %
  medium: number;
  wide: number;
}

export interface StylePrefs {
  language: Language;
  target_duration_sec: number;              // durée cible rough cut (60-600)
  cutting_style: CuttingStyle;
  framing_mix: FramingMix;
  auto_cleanup_silences: boolean;
  auto_remove_hesitations: boolean;
  suggest_proactively: boolean;
  min_scene_duration_sec: number;
}

export const DEFAULT_STYLE_PREFS: StylePrefs = {
  language: "de",
  target_duration_sec: 90,
  cutting_style: "moderate",
  framing_mix: { closeup: 40, medium: 40, wide: 20 },
  auto_cleanup_silences: true,
  auto_remove_hesitations: false,
  suggest_proactively: true,
  min_scene_duration_sec: 1.0,
};

interface StylePrefsState {
  prefs: StylePrefs;
  setPref: <K extends keyof StylePrefs>(key: K, value: StylePrefs[K]) => void;
  setFramingMix: (mix: FramingMix) => void;
  reset: () => void;
  /** Diff des prefs par rapport aux défauts — pratique pour un badge « aktiv ». */
  changedCount: () => number;
}

export const useStylePrefsStore = create<StylePrefsState>()(
  persist(
    (set, get) => ({
      prefs: DEFAULT_STYLE_PREFS,
      setPref: (key, value) => set((s) => ({ prefs: { ...s.prefs, [key]: value } })),
      setFramingMix: (mix) => set((s) => ({ prefs: { ...s.prefs, framing_mix: mix } })),
      reset: () => set({ prefs: DEFAULT_STYLE_PREFS }),
      changedCount: () => {
        const p = get().prefs;
        let n = 0;
        for (const k of Object.keys(DEFAULT_STYLE_PREFS) as Array<keyof StylePrefs>) {
          if (k === "framing_mix") {
            const a = p.framing_mix, b = DEFAULT_STYLE_PREFS.framing_mix;
            if (a.closeup !== b.closeup || a.medium !== b.medium || a.wide !== b.wide) n++;
          } else if (p[k] !== DEFAULT_STYLE_PREFS[k]) {
            n++;
          }
        }
        return n;
      },
    }),
    { name: "cinassist-style-prefs" },
  ),
);
