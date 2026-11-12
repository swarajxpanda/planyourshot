import { useEffect, useRef, useState } from "react";
import type { UsageRow } from "../types";
import { formatDuration, type UsageState } from "../util";
import { Icon, Spinner } from "./ui";

const WARN_AT = 0.8;

/** used / limit, or null when the plan doesn't include it (limit 0). */
const ratio = (r: UsageRow) => (r.limit > 0 ? r.used / r.limit : null);
const level = (r: UsageRow) => {
  const x = ratio(r);
  return x === null ? "" : x >= 1 ? "full" : x >= WARN_AT ? "high" : "";
};

/**
 * Top-bar button with a popover of the plan's limits. Everything shown about the plan —
 * names, labels, numbers, features — comes from GET /api/me/usage.
 */
export default function UsageButton({ state }: { state: UsageState }) {
  const { usage, fetchedAt, loading, error, refresh } = state;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const atLimit = !!usage?.rows.some((r) => level(r) === "full");
  const toggle = () => {
    if (!open) refresh(); // always current when opened
    setOpen((o) => !o);
  };

  return (
    <div className="usage" ref={ref}>
      <button className="btn ghost small usage-btn" aria-haspopup="dialog" aria-expanded={open} onClick={toggle}
        title={atLimit ? "A usage limit has been reached" : undefined}>
        <Icon name="usage" size={15} /> <span className="hide-narrow">Usage</span>
        {atLimit && <span className="usage-dot" aria-label="A usage limit has been reached" />}
      </button>
      {open && (
        <div className="usage-pop" role="dialog" aria-label="Usage">
          <div className="usage-head">
            <h2>Usage</h2>
            {usage && <span className="muted">{usage.plan_name} plan</span>}
            {loading && usage && <Spinner small />}
          </div>
          {error && <p className="inline-error" role="alert">{error}</p>}
          {!usage && loading && <div className="usage-loading"><Spinner /></div>}
          {usage && (
            <>
              <ul className="usage-rows">
                {usage.rows.map((r) => <Row key={r.key} row={r} fetchedAt={fetchedAt} />)}
              </ul>
              {usage.features.map((f) => (
                <p key={f.key} className="usage-feature">
                  <Icon name={f.included ? "check" : "x"} size={13} />
                  {f.label} {f.included ? "is included" : "isn't included"} in the {usage.plan_name} plan.
                </p>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ row, fetchedAt }: { row: UsageRow; fetchedAt: number }) {
  const x = ratio(row);
  const remaining = row.reset_in_seconds === null ? null : row.reset_in_seconds - (Date.now() - fetchedAt) / 1000;
  return (
    <li className="usage-row">
      <div className="usage-line">
        <span>{row.label}</span>
        <span className="usage-count">{x === null ? "Not included" : `${row.used} of ${row.limit}`}</span>
      </div>
      {x !== null && (
        <div className="usage-track" role="progressbar" aria-label={row.label} aria-valuemin={0} aria-valuemax={row.limit}
          aria-valuenow={Math.min(row.used, row.limit)}>
          <div className={`usage-fill ${level(row)}`} style={{ width: `${Math.min(1, x) * 100}%` }} />
        </div>
      )}
      {row.window && row.used > 0 && remaining !== null && (
        <small className="usage-reset">Next resets in {formatDuration(remaining)}</small>
      )}
    </li>
  );
}
