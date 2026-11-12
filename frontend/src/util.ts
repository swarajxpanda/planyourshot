import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
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

/** The signed-in user's plan, limits and current usage. `refresh` after anything that uses a quota. */
export function useUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const refresh = useCallback(() => { api.usage().then(setUsage).catch(() => undefined); }, []);
  useEffect(refresh, [refresh]);
  return [usage, refresh] as const;
}
