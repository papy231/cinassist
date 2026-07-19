// wave-peaks-cache.ts — cache de peaks audio partagé par source (Layer 3, UI only).
//
// Golden rule : ceci est de la pure visualisation. Aucune de ces valeurs ne
// pilote la lecture ni l'horloge. La timeline reste la seule source de vérité.
//
// Problème résolu : un même fichier média peut apparaître comme plusieurs TLClip
// (split / duplicate). Décoder l'audio N fois serait du gaspillage. On mémorise
// donc les peaks PAR source (clé = proxyUrl/src) et on les partage entre tous
// les clips qui pointent sur la même source. 13 clips / 5 sources → 5 décodages.

/** Nombre de points de peaks calculés pour la source ENTIÈRE. Les clips
 *  découpent ensuite une sous-fenêtre de ce tableau selon mediaStart/duration. */
const DEFAULT_SAMPLES = 2000;

/** Cache module-level : src → Promise<Float32Array>. On garde la Promise pour
 *  que des appels concurrents pendant le décodage se partagent le même travail. */
const cache = new Map<string, Promise<Float32Array>>();

let audioCtx: AudioContext | null = null;

function getAudioContext(): AudioContext {
  if (!audioCtx) {
    // Compat Safari : webkitAudioContext.
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    audioCtx = new Ctor();
  }
  return audioCtx;
}

/**
 * Downsample RMS-abs du canal 0 vers `samples` points.
 *
 * Pour chaque bucket on calcule la racine de la moyenne des carrés (RMS), ce qui
 * donne une enveloppe plus douce et « pleine » qu'un simple min/max — rendu type
 * DaVinci, moins bruité que le pic brut. Valeurs dans [0, 1].
 */
function downsampleRMS(channel: Float32Array, samples: number): Float32Array {
  const out = new Float32Array(samples);
  const blockSize = channel.length / samples;
  for (let i = 0; i < samples; i++) {
    const start = Math.floor(i * blockSize);
    const end = Math.min(channel.length, Math.floor((i + 1) * blockSize));
    let sumSq = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      const v = channel[j];
      sumSq += v * v;
      count++;
    }
    out[i] = count > 0 ? Math.sqrt(sumSq / count) : 0;
  }
  return out;
}

/**
 * Récupère (et met en cache) les peaks RMS de la source ENTIÈRE.
 *
 * @param src      URL de la source (proxyUrl de préférence). Sert de clé de cache.
 * @param samples  Résolution du tableau de peaks pour la source complète.
 * @returns        Float32Array de `samples` points dans [0,1]. En cas d'échec
 *                 (fetch/decode), renvoie un Float32Array VIDE (length 0) et log
 *                 un warning — le caller retombe alors sur le PNG existant.
 */
export function getPeaks(
  src: string,
  samples: number = DEFAULT_SAMPLES
): Promise<Float32Array> {
  if (!src) return Promise.resolve(new Float32Array(0));

  const cached = cache.get(src);
  if (cached) return cached;

  const promise = (async () => {
    try {
      // Same-origin via rewrite Next.js (/proxies/*, /uploads/* → :8001).
      // Donc pas de CORS sur le fetch ni sur decodeAudioData.
      const res = await fetch(src);
      if (!res.ok) throw new Error(`HTTP ${res.status} for ${src}`);
      const arrayBuffer = await res.arrayBuffer();
      const ctx = getAudioContext();
      // decodeAudioData copie/détache le buffer ; ok, on ne le réutilise pas.
      const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
      const channel = audioBuffer.getChannelData(0);
      return downsampleRMS(channel, samples);
    } catch (err) {
      console.warn(`[wave-peaks-cache] peaks failed for ${src}:`, err);
      // On garde la Promise résolue (empty) en cache : pas de re-fetch en boucle.
      return new Float32Array(0);
    }
  })();

  cache.set(src, promise);
  return promise;
}

/**
 * Découpe la sous-fenêtre de peaks correspondant à [mediaStart, mediaStart+duration]
 * dans la source. `fullPeaks` couvre `sourceDuration` secondes sur toute sa longueur.
 *
 * Si sourceDuration est inconnu/0, on renvoie le tableau complet (le clip couvre
 * alors toute la source visuellement — acceptable en fallback).
 */
export function slicePeaks(
  fullPeaks: Float32Array,
  mediaStart: number,
  duration: number,
  sourceDuration: number
): Float32Array {
  if (fullPeaks.length === 0) return fullPeaks;
  if (!(sourceDuration > 0) || !(duration > 0)) return fullPeaks;

  const n = fullPeaks.length;
  const startIdx = Math.max(0, Math.floor((mediaStart / sourceDuration) * n));
  const endIdx = Math.min(
    n,
    Math.ceil(((mediaStart + duration) / sourceDuration) * n)
  );
  if (endIdx <= startIdx) return fullPeaks.subarray(startIdx, startIdx + 1);
  return fullPeaks.subarray(startIdx, endIdx);
}

/** Nettoyage optionnel (tests / HMR) : ferme l'AudioContext et vide le cache. */
export function _resetPeaksCache(): void {
  cache.clear();
  if (audioCtx) {
    void audioCtx.close();
    audioCtx = null;
  }
}
