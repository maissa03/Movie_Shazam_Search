"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Polling HTTP simple : appelle `fetcher` toutes les `intervalMs` tant que `active` est vrai.
 * Une seule requête à la fois (enchaînement par setTimeout, pas setInterval), arrêt au démontage.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number, active = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refresh = useCallback(async () => {
    try {
      const value = await fetcherRef.current();
      setData(value);
      setError(null);
      return value;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await refresh();
      if (!cancelled && active) timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh, intervalMs, active]);

  return { data, error, loading, refresh, setData };
}
