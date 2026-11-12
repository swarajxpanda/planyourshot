import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api";
import type { Usage } from "./types";

export function formatDate(iso: string): string {
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (days < 1 && d.getDate() === new Date().getDate()) return `Today, ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }) + `, ${time}`;
}

export function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** Run `fn` every `ms` while `active` is true. */
export function usePolling(fn: () => void, active: boolean, ms = 1500) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}

/**
 * useState that is remembered in localStorage. `parse` validates what was stored and returns
 * undefined to fall back to `initial`. Storage can be unavailable (private mode, blocked site
 * data), so every access is guarded and the state still works in memory.
 */
export function usePersistentState<T>(key: string, initial: T, parse: (raw: unknown) => T | undefined) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw !== null) {
        const v = parse(JSON.parse(raw));
        if (v !== undefined) return v;
      }
    } catch { /* unavailable or corrupt */ }
    return initial;
  });
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* unavailable */ }
  }, [key, value]);
  return [value, setValue] as const;
}

/** Parser for usePersistentState: a finite number clamped to [min, max]. */
export const storedNumber = (min: number, max: number) => (raw: unknown) =>
  typeof raw === "number" && Number.isFinite(raw) ? Math.min(max, Math.max(min, Math.round(raw))) : undefined;
export const storedBoolean = (raw: unknown) => (typeof raw === "boolean" ? raw : undefined);

export interface UsageState {
  usage: Usage | null;
  /** Date.now() when `usage` arrived; reset countdowns are measured from here */
  fetchedAt: number;
  loading: boolean;
  error: string;
  refresh: () => void;
}

/** The signed-in user's plan, limits and current usage. Call `refresh` after anything that uses a quota. */
export function useUsage(): UsageState {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(0); // ignore answers that arrive after a newer request
  const refresh = useCallback(() => {
    const n = ++latest.current;
    setLoading(true);
    api.usage()
      .then((u) => { if (n === latest.current) { setUsage(u); setFetchedAt(Date.now()); setError(""); } })
      .catch((e) => { if (n === latest.current) setError(errorText(e)); })
      .finally(() => { if (n === latest.current) setLoading(false); });
  }, []);
  useEffect(refresh, [refresh]);
  return { usage, fetchedAt, loading, error, refresh };
}

/** "3h 20m", "29d 23h", "45m", "under a minute". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return "under a minute";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return h ? `${d}d ${h}h` : `${d}d`;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}
