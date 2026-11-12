import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorText } from "../api";
import { useAuth } from "../App";
import { Confirm, Dialog, Icon, Menu, Spinner, Toast, Wordmark } from "../components/ui";
import type { Script } from "../types";
import { formatDate, formatSize, usePersistentState, usePolling, useUsage } from "../util";

const ACCEPT = ".pdf,.docx,.txt";
type View = "list" | "grid";
const storedView = (raw: unknown) => (raw === "list" || raw === "grid" ? raw : undefined);

export function AppBar({ children }: { children?: React.ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState(false);
  return (
    <header className="appbar">
      <Link to="/" className="appbar-brand"><Wordmark /></Link>
      {children}
      <div className="appbar-user">
        <Menu
          label="Account"
          items={[
            { label: `Signed in as ${user.email}`, onSelect: () => undefined, disabled: true },
            { label: "Export my data", onSelect: () => { window.location.href = api.exportUrl; } },
            ...(user.is_admin ? [{ label: "Admin", onSelect: () => navigate("/admin") }] : []),
            { label: "Terms of Service", onSelect: () => navigate("/terms") },
            { label: "Privacy Policy", onSelect: () => navigate("/privacy") },
            { label: "Sign out", onSelect: logout },
            { label: "Delete account…", onSelect: () => setDeleting(true), danger: true },
          ]}
        >
          {user.picture ? <img className="avatar" src={user.picture} alt="" referrerPolicy="no-referrer" />
            : <span className="avatar">{(user.name || user.email)[0].toUpperCase()}</span>}
        </Menu>
      </div>
      {deleting && <DeleteAccountDialog onClose={() => setDeleting(false)} onDeleted={logout} />}
    </header>
  );
}

function DeleteAccountDialog({ onClose, onDeleted }: { onClose: () => void; onDeleted: () => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (typed !== "DELETE") return;
    setBusy(true);
    try {
      await api.deleteAccount();
      onDeleted();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };
  return (
    <Dialog title="Delete account" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-body">
          <p>
            This permanently deletes your account and everything in it: every screenplay and its original file, all
            versions, conversations, shot lists and usage history. It can't be undone.
          </p>
          <p>
            Want a copy first? <a href={api.exportUrl}>Export your data</a>.
          </p>
          <label htmlFor="confirm-delete" className="field-label">Type DELETE to confirm</label>
          <input id="confirm-delete" className="field" value={typed} autoFocus autoComplete="off" spellCheck={false}
            onChange={(e) => setTyped(e.target.value)} />
          {error && <p className="inline-error" role="alert">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn danger" disabled={busy || typed !== "DELETE"}>{busy ? <Spinner small /> : "Delete account"}</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [scripts, setScripts] = useState<Script[] | null>(null);
  const [listLimit, setLimit] = useState(user.max_scripts);
  const [usage, refreshUsage] = useUsage();
  const limit = usage?.limits.max_scripts ?? listLimit;
  const [loadError, setLoadError] = useState("");
  const [toast, setToast] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [renaming, setRenaming] = useState<Script | null>(null);
  const [deleting, setDeleting] = useState<Script | null>(null);
  const [view, setView] = usePersistentState<View>("pys.dashboardView", "list", storedView);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.scripts();
      setScripts(data.scripts);
      setLimit(data.limit);
      setLoadError("");
      refreshUsage();
    } catch (e) {
      setLoadError(errorText(e));
    }
  }, [refreshUsage]);
  useEffect(() => { load(); }, [load]);
  usePolling(load, !!scripts?.some((s) => s.status === "processing" || s.shotlist_status === "generating"));

  const full = (scripts?.length ?? 0) >= limit;

  const upload = async (file: File | undefined) => {
    if (!file || uploading) return;
    if (full) return setToast(`You've reached the limit of ${limit} screenplays${usage ? ` on the ${usage.plan_name} plan` : ""}. Delete one to upload another.`);
    if (!/\.(pdf|docx|txt)$/i.test(file.name)) return setToast("Unsupported file type. Upload a PDF, DOCX or TXT screenplay.");
    if (file.size > user.max_upload_mb * 1024 * 1024) return setToast(`File is too large. The limit is ${user.max_upload_mb} MB.`);
    setUploading(true);
    try {
      await api.upload(file);
      await load();
    } catch (e) {
      setToast(errorText(e));
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div className="page"
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files[0]); }}>
      <AppBar />
      <main className="dashboard">
        <div className="dash-head">
          <div>
            <h1>Screenplays</h1>
            <p className="muted">
              {scripts ? `${scripts.length} of ${limit} ${limit === 1 ? "screenplay" : "screenplays"}${usage ? ` · ${usage.plan_name} plan` : ""}` : " "}
              {full && " · Delete a screenplay to upload another"}
            </p>
          </div>
          <div className="dash-actions">
            {scripts && scripts.length > 0 && (
              <div className="segmented" role="group" aria-label="View">
                <button aria-pressed={view === "list"} onClick={() => setView("list")} title="List view"><Icon name="list" size={15} /><span className="sr-only">List</span></button>
                <button aria-pressed={view === "grid"} onClick={() => setView("grid")} title="Grid view"><Icon name="grid" size={15} /><span className="sr-only">Grid</span></button>
              </div>
            )}
            <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => upload(e.target.files?.[0])} />
            <button className="btn primary" disabled={uploading || full || !scripts} onClick={() => input.current?.click()}>
              {uploading ? <Spinner small /> : <Icon name="upload" />} {uploading ? "Uploading…" : "Upload screenplay"}
            </button>
          </div>
        </div>

        {loadError && (
          <div className="notice"><p>{loadError}</p><button className="btn" onClick={load}>Try again</button></div>
        )}
        {!scripts && !loadError && (
          <div className="script-list" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="script-row skeleton" />)}</div>
        )}

        {scripts?.length === 0 && (
          <button className={dragging ? "dropzone over" : "dropzone"} onClick={() => input.current?.click()} disabled={uploading}>
            <Icon name="doc" size={28} />
            <strong>Upload your first screenplay</strong>
            <span>Drop a PDF, DOCX or TXT here, or click to choose a file. Up to {user.max_upload_mb} MB.</span>
          </button>
        )}

        {scripts && scripts.length > 0 && (
          <>
            <div className={view === "grid" ? "script-list grid" : "script-list"}>
              <div className="script-row head" aria-hidden="true">
                <span>Title</span><span>Status</span><span>Shots</span><span>Versions</span><span>Uploaded</span><span />
              </div>
              {scripts.map((s) => (
                <div key={s.id} className={s.status === "ready" ? "script-row link" : "script-row"}
                  role={s.status === "ready" ? "link" : undefined} tabIndex={s.status === "ready" ? 0 : undefined}
                  onClick={() => s.status === "ready" && navigate(`/s/${s.id}`)}
                  onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && s.status === "ready" && navigate(`/s/${s.id}`)}>
                  <span className="script-title">
                    <strong>{s.title}</strong>
                    <small>{s.original_filename} · {s.file_type.toUpperCase()} · {formatSize(s.file_size)}</small>
                  </span>
                  <span data-label="Status">
                    {s.status === "processing" && <span className="status working"><Spinner small /> Processing</span>}
                    {s.status === "failed" && <span className="status failed" title={s.error}>Failed</span>}
                    {s.status === "ready" && (s.shotlist_status === "generating"
                      ? <span className="status working"><Spinner small /> Generating shots</span>
                      : <span className="status ready">Ready</span>)}
                  </span>
                  <span data-label="Shots" className="num">{s.status === "ready" ? s.shot_count || "—" : "—"}</span>
                  <span data-label="Versions" className="num">{s.status === "ready" ? s.revision_count : "—"}</span>
                  <span data-label="Uploaded" className="muted">{formatDate(s.created_at)}</span>
                  <span className="row-actions">
                    <Menu items={[
                      { label: "Open", onSelect: () => navigate(`/s/${s.id}`), disabled: s.status !== "ready" },
                      { label: "Open shot list", onSelect: () => navigate(`/s/${s.id}?tab=shots`), disabled: s.status !== "ready" },
                      { label: "Rename", onSelect: () => setRenaming(s) },
                      { label: "Download original", onSelect: () => { window.location.href = `/api/scripts/${s.id}/file`; } },
                      ...(s.status === "failed" ? [{ label: "Retry processing", onSelect: () => api.reprocess(s.id).then(load).catch((e) => setToast(errorText(e))) }] : []),
                      { label: "Delete", onSelect: () => setDeleting(s), danger: true },
                    ]} />
                  </span>
                  {s.status === "failed" && <p className="row-error">{s.error}</p>}
                </div>
              ))}
            </div>
            {dragging && !full && <div className="drop-hint">Drop to upload</div>}
          </>
        )}
      </main>

      {renaming && <RenameDialog script={renaming} onClose={() => setRenaming(null)} onDone={load} />}
      {deleting && (
        <Confirm title="Delete screenplay" confirmLabel="Delete" danger onClose={() => setDeleting(null)}
          body={<p>Delete <strong>{deleting.title}</strong>? Its conversations, versions and shot list will be permanently removed.</p>}
          onConfirm={async () => {
            try { await api.remove(deleting.id); await load(); } catch (e) { setToast(errorText(e)); }
          }} />
      )}
      {toast && <Toast message={toast} onDone={() => setToast("")} />}
    </div>
  );
}

export function RenameDialog({ script, onClose, onDone }: { script: Script; onClose: () => void; onDone: (s: Script) => void }) {
  const [title, setTitle] = useState(script.title);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      onDone(await api.rename(script.id, title.trim()));
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };
  return (
    <Dialog title="Rename screenplay" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-body">
          <input className="field" value={title} maxLength={200} autoFocus onChange={(e) => setTitle(e.target.value)} onFocus={(e) => e.target.select()} aria-label="Title" />
          {error && <p className="inline-error">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !title.trim()}>Save</button>
        </div>
      </form>
    </Dialog>
  );
}
