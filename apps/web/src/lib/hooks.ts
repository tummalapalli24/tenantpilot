"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

/** Fetches a GET endpoint; `reload()` re-fetches. Pass path=null to skip. */
export function useApi<T>(path: string | null, tenant?: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    api<T>(path, { tenant })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [path, tenant, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, loading: data === null && error === null, reload, setData };
}
