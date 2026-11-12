import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, errorText } from "../api";
import ChatPanel from "../components/ChatPanel";
import ResizeHandle from "../components/ResizeHandle";
import ScreenplayView from "../components/ScreenplayView";
import ShotList, { ShotDatalists } from "../components/ShotList";
import { Confirm, Dialog, Icon, Spinner, Toast } from "../components/ui";
import UsageButton from "../components/UsageButton";
import type { Revision, Scene, Script, ShotList as ShotListData } from "../types";
import { formatDate, storedBoolean, storedNumber, usePersistentState, usePolling, useUsage } from "../util";
import { AppBar, RenameDialog } from "./Dashboard";

// Column limits for the wide (> 1100px) layout. Below that the outline is hidden and the assistant is an overlay.
const OUTLINE = { min: 160, max: 360, default: 208 };
const ASSISTANT = { min: 320, max: 640, default: 384 };
const STAGE_MIN = 480;
const RAIL = 40; // collapsed outline

const SOURCE_LABEL: Record<string, string> = { upload: "Original", ai_edit: "AI edit", manual: "Manual edit", restore: "Restore" };

export default function Workspace() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "shots" ? "shots" : "script";
  const [script, setScript] = useState<Script | null>(null);
  const [shots, setShots] = useState<ShotListData | null>(null);
  const [fatal, setFatal] = useState<{ status: number; message: string } | null>(null);
  const [toast, setToast] = useState("");
  const [assistant, setAssistant] = useState(() => window.innerWidth > 1100);
  const [history, setHistory] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [viewing, setViewing] = useState<(Revision & { content: string }) | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const usageState = useUsage();
  const { usage, refresh: refreshUsage } = usageState;
  const [outlineWidth, setOutlineWidth] = usePersistentState("pys.outlineWidth", OUTLINE.default, storedNumber(OUTLINE.min, OUTLINE.max));
  const [assistantWidth, setAssistantWidth] = usePersistentState("pys.assistantWidth", ASSISTANT.default, storedNumber(ASSISTANT.min, ASSISTANT.max));
  const [outlineCollapsed, setOutlineCollapsed] = usePersistentState("pys.outlineCollapsed", false, storedBoolean);
  const [workspaceEl, setWorkspaceEl] = useState<HTMLDivElement | null>(null);
  const [workspaceWidth, setWorkspaceWidth] = useState(() => window.innerWidth);

  useEffect(() => {
    if (!workspaceEl) return;
    const ro = new ResizeObserver(([entry]) => setWorkspaceWidth(entry.contentRect.width));
    ro.observe(workspaceEl);
    return () => ro.disconnect();
  }, [workspaceEl]);

  const load = useCallback(async () => {
    try {
      const s = await api.script(id);
      setScript(s);
      if (s.status === "ready") setShots(await api.shots(id));
    } catch (e) {
      setFatal({ status: e instanceof ApiError ? e.status : 0, message: errorText(e) });
    }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  usePolling(load, script?.status === "processing");
  usePolling(() => api.shots(id).then(setShots).catch(() => undefined), shots?.status === "generating");
  // A generation uses quota as it runs: refresh usage when it starts and when it finishes.
  const shotStatus = shots?.status;
  useEffect(() => { if (shotStatus) refreshUsage(); }, [shotStatus, refreshUsage]);

  useEffect(() => { document.title = script ? `${script.title} — Plan Your Shot` : "Plan Your Shot"; return () => { document.title = "Plan Your Shot"; }; }, [script]);

  const onScreenplayChanged = useCallback((revision: Revision & { content: string }, scenes: Scene[]) => {
    setScript((s) => (s ? { ...s, revision, scenes, revision_count: s.revision_count + 1 } : s));
    setViewing(null);
  }, []);

  if (fatal)
    return (
      <div className="page"><AppBar />
        <div className="center-fill"><div className="notice">
          <p>{fatal.status === 404 ? "This screenplay doesn't exist or isn't in your workspace." : fatal.message}</p>
          <div className="row center">
            {fatal.status !== 404 && <button className="btn" onClick={() => { setFatal(null); load(); }}>Try again</button>}
            <Link className="btn" to="/">Back to screenplays</Link>
          </div>
        </div></div>
      </div>
    );
  if (!script) return <div className="page"><AppBar /><div className="center-fill"><Spinner /></div></div>;

  if (script.status !== "ready")
    return (
      <div className="page"><AppBar />
        <div className="center-fill"><div className="notice">
          {script.status === "processing"
            ? <><Spinner /><p>Reading <strong>{script.title}</strong>…</p></>
            : <><p><strong>{script.title}</strong> couldn't be processed.</p><p className="muted">{script.error}</p>
                <div className="row center">
                  <button className="btn" onClick={() => api.reprocess(id).then(setScript).catch((e) => setToast(errorText(e)))}>Retry</button>
                  <Link className="btn" to="/">Back to screenplays</Link>
                </div></>}
        </div></div>
        {toast && <Toast message={toast} onDone={() => setToast("")} />}
      </div>
    );

  const revision = script.revision!;
  const scenes = script.scenes!;
  const shown = viewing ?? revision;

  const saveEdit = async () => {
    if (editing === null) return;
    setSaving(true);
    try {
      setScript(await api.saveContent(id, editing));
      setEditing(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setEditing(null); // nothing changed
      else setToast(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  // Fit the saved widths to the space available so the stage never drops below STAGE_MIN:
  // the assistant gives way first, then the outline. Saved preferences are left untouched.
  const room = workspaceWidth - STAGE_MIN;
  let outlineW = outlineCollapsed ? RAIL : outlineWidth;
  let assistantW = assistant ? assistantWidth : 0;
  if (assistant && outlineW + assistantW > room) assistantW = Math.max(ASSISTANT.min, room - outlineW);
  if (!outlineCollapsed && outlineW + assistantW > room) outlineW = Math.max(OUTLINE.min, room - assistantW);
  const outlineMax = Math.max(OUTLINE.min, Math.min(OUTLINE.max, room - assistantW));
  const assistantMax = Math.max(ASSISTANT.min, Math.min(ASSISTANT.max, room - outlineW));
  // Handles sit in zero-width tracks between the panels.
  const columns = [`${outlineW}px`, !outlineCollapsed && "0px", `minmax(${STAGE_MIN}px, 1fr)`, assistant && "0px", assistant && `${assistantW}px`]
    .filter(Boolean).join(" ");

  const jump = (n: number) => document.getElementById(tab === "shots" ? `shots-scene-${n}` : `scene-${n}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="page workspace-page">
      <AppBar>
        <div className="crumb">
          <Link to="/" className="icon-btn" aria-label="Back to screenplays"><Icon name="back" /></Link>
          <button className="crumb-title" onClick={() => setRenaming(true)} title="Rename">{script.title}</button>
          <span className="muted version-tag">v{revision.number}</span>
        </div>
        <nav className="tabs" aria-label="Workspace">
          <button aria-current={tab === "script"} onClick={() => setParams({})}>Screenplay</button>
          <button aria-current={tab === "shots"} onClick={() => setParams({ tab: "shots" })}>
            Shot list {shots && shots.shots.length > 0 && <span className="count">{shots.shots.length}</span>}
          </button>
        </nav>
        <div className="appbar-tools">
          <UsageButton state={usageState} />
          <button className="btn ghost small" onClick={() => setHistory(true)}><Icon name="history" size={15} /> <span className="hide-narrow">Versions</span></button>
          <button className={assistant ? "btn small active" : "btn small"} aria-pressed={assistant} onClick={() => setAssistant((a) => !a)}>
            <Icon name="chat" size={15} /> <span className="hide-narrow">Assistant</span>
          </button>
        </div>
      </AppBar>

      <div ref={setWorkspaceEl} className={assistant ? "workspace with-assistant" : "workspace"}
        style={{ "--cols": columns } as React.CSSProperties}>
        {outlineCollapsed ? (
          <div className="outline-rail">
            <button className="icon-btn" aria-label="Show scene outline" aria-expanded={false} title="Show scenes"
              onClick={() => setOutlineCollapsed(false)}><Icon name="sidebar" /></button>
          </div>
        ) : (
          <>
            <nav className="outline" id="scene-outline" aria-label="Scenes">
              <div className="outline-head">
                <span className="outline-title">Scenes</span>
                <button className="icon-btn" aria-label="Hide scene outline" aria-expanded={true} aria-controls="scene-outline" title="Hide scenes"
                  onClick={() => setOutlineCollapsed(true)}><Icon name="sidebar" /></button>
              </div>
              {scenes.map((s) => (
                <button key={s.number} className="outline-item" onClick={() => jump(s.number)} title={s.heading}>
                  <span className="num">{s.number}</span><span className="outline-text">{s.heading}</span>
                </button>
              ))}
            </nav>
            <ResizeHandle label="Resize scene outline" controls="scene-outline" direction={1}
              value={outlineW} min={OUTLINE.min} max={outlineMax} defaultValue={OUTLINE.default} onChange={setOutlineWidth} />
          </>
        )}

        <main className="stage">
          {tab === "script" ? (
            <>
              <div className="stage-bar">
                {viewing ? (
                  <>
                    <span className="banner">Viewing version {viewing.number} · {SOURCE_LABEL[viewing.source]} · {formatDate(viewing.created_at)}</span>
                    <span className="spacer" />
                    <button className="btn ghost small" onClick={() => setViewing(null)}>Back to current</button>
                    <button className="btn small" onClick={() => api.restore(id, viewing.id).then((s) => { setScript(s); setViewing(null); }).catch((e) => setToast(errorText(e)))}>Restore this version</button>
                  </>
                ) : editing !== null ? (
                  <>
                    <span className="banner">Editing — saved as a new version</span>
                    <span className="spacer" />
                    <button className="btn ghost small" onClick={() => setEditing(null)} disabled={saving}>Cancel</button>
                    <button className="btn primary small" onClick={saveEdit} disabled={saving || !editing.trim()}>{saving ? <Spinner small /> : "Save version"}</button>
                  </>
                ) : (
                  <>
                    <span className="muted">{scenes.length} {scenes.length === 1 ? "scene" : "scenes"} · {Math.max(1, Math.round(revision.content.split(/\s+/).length / 180))} min read</span>
                    <span className="spacer" />
                    <button className="btn ghost small" onClick={() => setEditing(revision.content)}><Icon name="edit" size={14} /> Edit text</button>
                  </>
                )}
              </div>
              {editing !== null && !viewing
                ? <textarea className="screenplay editor" value={editing} spellCheck onChange={(e) => setEditing(e.target.value)} aria-label="Screenplay text" />
                : <ScreenplayView content={shown.content} scenes={viewing ? [] : scenes} />}
            </>
          ) : shots ? (
            <ShotList scriptId={id} scenes={scenes} data={shots} setData={setShots} onError={setToast}
              usage={usage} onUsageChanged={refreshUsage} />
          ) : <div className="center-fill"><Spinner /></div>}
        </main>

        {assistant && (
          <ResizeHandle label="Resize assistant" controls="assistant-panel" direction={-1}
            value={assistantW} min={ASSISTANT.min} max={assistantMax} defaultValue={ASSISTANT.default} onChange={setAssistantWidth} />
        )}
        {assistant && (
          <ChatPanel scriptId={id} channel={tab} hasShots={!!shots?.shots.length} generating={shots?.status === "generating"}
            onScreenplayChanged={onScreenplayChanged} onShotsChanged={setShots} onClose={() => setAssistant(false)}
            onUsageChanged={refreshUsage} />
        )}
      </div>

      <ShotDatalists />
      {history && (
        <HistoryDialog scriptId={id} onClose={() => setHistory(false)}
          onView={(r) => { setViewing(r.id === revision.id ? null : r); setEditing(null); setParams({}); setHistory(false); }}
          onRestored={(s) => { setScript(s); setViewing(null); setHistory(false); }} />
      )}
      {renaming && <RenameDialog script={script} onClose={() => setRenaming(false)} onDone={(s) => setScript((prev) => (prev ? { ...prev, title: s.title } : prev))} />}
      {toast && <Toast message={toast} onDone={() => setToast("")} />}
    </div>
  );
}

function HistoryDialog({ scriptId, onClose, onView, onRestored }: {
  scriptId: string; onClose: () => void; onView: (r: Revision & { content: string }) => void; onRestored: (s: Script) => void;
}) {
  const [data, setData] = useState<{ revisions: Revision[]; current_revision_id: number } | null>(null);
  const [error, setError] = useState("");
  const [restoring, setRestoring] = useState<Revision | null>(null);
  useEffect(() => { api.revisions(scriptId).then(setData).catch((e) => setError(errorText(e))); }, [scriptId]);

  if (restoring)
    return (
      <Confirm title={`Restore version ${restoring.number}`} confirmLabel="Restore" onClose={() => setRestoring(null)}
        body={<p>The screenplay will go back to this version. Nothing is lost — the restore is saved as a new version on top of the history.</p>}
        onConfirm={async () => { try { onRestored(await api.restore(scriptId, restoring.id)); } catch (e) { setError(errorText(e)); } }} />
    );
  return (
    <Dialog title="Version history" onClose={onClose} wide>
      <div className="dialog-body history">
        {error && <p className="inline-error">{error}</p>}
        {!data && !error && <Spinner />}
        {data?.revisions.map((r) => {
          const current = r.id === data.current_revision_id;
          return (
            <div key={r.id} className="history-row">
              <span className="num">v{r.number}</span>
              <div>
                <strong>{r.summary || SOURCE_LABEL[r.source]}</strong>
                <small>{SOURCE_LABEL[r.source]} · {formatDate(r.created_at)}</small>
              </div>
              {current ? <span className="pill ok">Current</span> : (
                <span className="row">
                  <button className="btn ghost small" onClick={() => api.revision(scriptId, r.id).then(onView).catch((e) => setError(errorText(e)))}>View</button>
                  <button className="btn small" onClick={() => setRestoring(r)}>Restore</button>
                </span>
              )}
            </div>
          );
        })}
      </div>
    </Dialog>
  );
}
