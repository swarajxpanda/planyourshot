import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, errorText } from "../api";
import { Spinner, Toast } from "../components/ui";
import type { AdminOverview, AdminUser } from "../types";
import { formatDate } from "../util";
import { AppBar } from "./Dashboard";

/** Operator dashboard. The backend answers 404 to anyone not in ADMIN_EMAILS. */
export default function Admin() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<AdminUser[] | null>(null);

  const load = useCallback(() => {
    api.adminOverview().then(setData).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError(errorText(e));
    });
  }, []);
  useEffect(load, [load]);

  const toggle = async (u: AdminUser) => {
    try {
      const updated = await api.adminSetDisabled(u.id, !u.disabled);
      setFound((list) => list?.map((x) => (x.id === u.id ? { ...x, disabled: updated.disabled } : x)) ?? null);
      setToast(`${updated.email} is now ${updated.disabled ? "disabled" : "enabled"}.`);
      load();
    } catch (e) {
      setToast(errorText(e));
    }
  };
  const search = (e: React.FormEvent) => {
    e.preventDefault();
    api.adminUsers(query).then((r) => setFound(r.users)).catch((err) => setToast(errorText(err)));
  };

  if (missing)
    return (
      <div className="page"><AppBar />
        <div className="center-fill"><div className="notice"><p>Page not found.</p><Link className="btn" to="/">Back to screenplays</Link></div></div>
      </div>
    );

  const max = Math.max(1, ...(data?.days.map((d) => d.calls) ?? [1]));
  return (
    <div className="page"><AppBar />
      <main className="dashboard admin">
        <div className="dash-head"><div><h1>Admin</h1>{data && <p className="muted">{data.totals.users} users · {data.totals.scripts} screenplays</p>}</div></div>
        {error && <p className="inline-error" role="alert">{error}</p>}
        {!data && !error && <Spinner />}
        {data && (
          <>
            <section className="admin-cards">
              <div className="admin-card"><span className="muted">Queued jobs</span><strong>{data.queue.queued}</strong></div>
              <div className="admin-card"><span className="muted">Running</span><strong>{data.queue.running}</strong></div>
              <div className="admin-card"><span className="muted">Oldest queued</span><strong>{Math.round(data.queue.oldest_queued_seconds)}s</strong></div>
              <div className="admin-card"><span className="muted">Failed jobs</span><strong>{data.queue.failed_total}</strong></div>
            </section>

            <section>
              <h2>Model calls per day</h2>
              <table className="admin-table">
                <thead><tr><th>Day</th><th className="n">Calls</th><th className="n">Failed</th><th className="n">Refused</th><th aria-hidden="true" /></tr></thead>
                <tbody>
                  {data.days.length === 0 && <tr><td colSpan={5} className="muted">No model calls in the last 14 days.</td></tr>}
                  {data.days.map((d) => (
                    <tr key={d.day}>
                      <td className="num">{d.day}</td><td className="n">{d.calls}</td><td className="n">{d.failed}</td><td className="n">{d.refused}</td>
                      <td className="bar-cell"><span className="bar" style={{ width: `${(d.calls / max) * 100}%` }} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section>
              <h2>Top users, last 7 days</h2>
              <UserTable users={data.top_users} onToggle={toggle} showUsage />
            </section>

            <section>
              <h2>Find a user</h2>
              <form className="row" onSubmit={search}>
                <input className="field" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Email or name" aria-label="Search users" />
                <button className="btn">Search</button>
              </form>
              {found && <UserTable users={found} onToggle={toggle} />}
            </section>

            <section>
              <h2>Recent failed jobs</h2>
              <table className="admin-table">
                <thead><tr><th>Job</th><th>User</th><th className="n">Attempts</th><th>Error</th><th>When</th></tr></thead>
                <tbody>
                  {data.failed_jobs.length === 0 && <tr><td colSpan={5} className="muted">None.</td></tr>}
                  {data.failed_jobs.map((j) => (
                    <tr key={j.id}>
                      <td className="num">#{j.id} {j.kind}</td><td>{j.user_email ?? "—"}</td><td className="n">{j.attempts}</td>
                      <td className="admin-error">{j.error}</td><td className="muted">{formatDate(j.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </main>
      {toast && <Toast message={toast} onDone={() => setToast("")} />}
    </div>
  );
}

function UserTable({ users, onToggle, showUsage }: { users: AdminUser[]; onToggle: (u: AdminUser) => void; showUsage?: boolean }) {
  return (
    <table className="admin-table">
      <thead><tr><th>User</th><th>Plan</th>{showUsage && <><th className="n">Calls</th><th className="n">Characters</th></>}<th>Status</th><th /></tr></thead>
      <tbody>
        {users.length === 0 && <tr><td colSpan={6} className="muted">No users.</td></tr>}
        {users.map((u) => (
          <tr key={u.id}>
            <td><strong>{u.email}</strong><br /><small>{u.name}</small></td>
            <td>{u.plan}</td>
            {showUsage && <><td className="n">{u.calls}</td><td className="n">{u.chars.toLocaleString()}</td></>}
            <td>{u.disabled ? <span className="pill warn">Disabled</span> : <span className="pill ok">Active</span>}</td>
            <td className="n"><button className={u.disabled ? "btn small" : "btn small danger-outline"} onClick={() => onToggle(u)}>{u.disabled ? "Enable" : "Disable"}</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
