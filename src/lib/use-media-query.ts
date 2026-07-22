"use client";

/**
 * use-media-query.ts — hook léger pour tester une media query CSS.
 *
 * Utilisé pour le layout responsive d'Editor.tsx (mobile vs desktop) sans
 * ajouter de dépendance. SSR-safe : retourne `false` côté serveur puis se
 * réévalue au montage client.
 */

import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    // Safari <14 utilise addListener (déprécié) — fallback si addEventListener absent.
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", onChange);
      else mql.removeListener(onChange);
    };
  }, [query]);
  return matches;
}

/** Mobile portrait ou petit écran. Breakpoint aligné sur Tailwind `md`. */
export const useIsMobile = () => useMediaQuery("(max-width: 767px)");

/** Tablette portrait ou petit laptop. */
export const useIsTablet = () => useMediaQuery("(max-width: 1023px)");
