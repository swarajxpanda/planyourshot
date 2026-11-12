import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText, isLimitError } from "../api";
import type { Scene, Shot, ShotList as ShotListData, Usage } from "../types";
import { AutoTextarea, Confirm, Icon, Menu, Spinner } from "./ui";

const SIZES = ["ECU", "CU", "MCU", "MS", "MLS", "WS", "EWS"];
const SIZE_NAMES: Record<string, string> = {
  ECU: "Extreme close-up", CU: "Close-up", MCU: "Medium close-up", MS: "Medium shot",
  MLS: "Medium long shot", WS: "Wide shot", EWS: "Extreme wide shot",
};

interface Props {
  scriptId: string;
  scenes: Scene[];
  data: ShotListData;
  setData: (d: ShotListData) => void;
  onError: (msg: string) => void;
  usage: Usage | null;
  onUsageChanged: () => void;
}

interface Group {
  number: number;
  heading: string;
  inScript: boolean;
  shots: Shot[];
}

export default function ShotList({ scriptId, scenes, data, setData, onError, usage, onUsageChanged }: Props) {
  const [confirmAll, setConfirmAll] = useState(false);
  // Plan limits (429) and features off for the plan (403) are shown inline in the toolbar.
  const [limitError, setLimitError] = useState("");
  const [confirmScene, setConfirmScene] = useState<Group | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<{ scene: number; index: number } | null>(null);
  const generating = data.status === "generating";

  const groups = useMemo<Group[]>(() => {
    const map = new Map<number, Group>();
    scenes.forEach((s) => map.set(s.number, { number: s.number, heading: s.heading, inScript: true, shots: [] }));
    data.shots.forEach((s) => {
      if (!map.has(s.scene_number)) map.set(s.scene_number, { number: s.scene_number, heading: s.scene_heading, inScript: false, shots: [] });
      map.get(s.scene_number)!.shots.push(s);
    });
    return [...map.values()].sort((a, b) => a.number - b.number);
  }, [scenes, data.shots]);

  const report = (e: unknown) => (isLimitError(e) ? setLimitError(errorText(e)) : onError(errorText(e)));
  const run = async (fn: () => Promise<ShotListData>) => {
    try { setData(await fn()); setLimitError(""); } catch (e) { report(e); }
  };
  const patchLocal = (shot: Shot) => setData({ ...data, shots: data.shots.map((s) => (s.id === shot.id ? shot : s)) });
  const generate = (scene: number | null) => run(() => api.generateShots(scriptId, scene)).finally(onUsageChanged);
  const canExport = !!usage?.limits.csv_export;

  const move = (shot: Shot, scene: number, index: number) => run(() => api.moveShot(scriptId, shot.id, scene, index));
  const drop = () => {
    const shot = data.shots.find((s) => s.id === drag);
    if (shot && dropAt) move(shot, dropAt.scene, dropAt.index);
    setDrag(null);
    setDropAt(null);
  };

  if (data.shots.length === 0 && !generating) {
    return (
      <div className="shots-empty">
        <h2>No shot list yet</h2>
        <p>
          Generate a first pass from the screenplay — {scenes.length} {scenes.length === 1 ? "scene" : "scenes"}, broken down
          shot by shot with sizes, angles, movement and lenses. Everything stays editable.
        </p>
        {data.status === "failed" && data.error && <p className="inline-error" role="alert">{data.error}</p>}
        {limitError && <p className="inline-error" role="alert">{limitError}</p>}
        <div className="row center">
          <button className="btn primary" onClick={() => generate(null)}>
            {data.status === "failed" ? "Try again" : "Generate shot list"}
          </button>
          <button className="btn" onClick={() => run(() => api.addShot(scriptId, scenes[0].number, null))}>Start from a blank shot</button>
        </div>
      </div>
    );
  }

  return (
    <div className="shots">
      <div className="shots-bar">
        <div>
          <strong>{data.shots.length}</strong> {data.shots.length === 1 ? "shot" : "shots"} · {groups.filter((g) => g.shots.length).length} of {groups.length} scenes covered
        </div>
        <div className="row">
          {canExport && (
            <a className="btn small" href={`/api/scripts/${scriptId}/shots/export.csv`} aria-disabled={!data.shots.length}>
              <Icon name="download" size={14} /> Export CSV
            </a>
          )}
          <button className="btn small" disabled={generating} onClick={() => setConfirmAll(true)}>
            <Icon name="refresh" size={14} /> Regenerate all
          </button>
        </div>
      </div>
      {limitError && <p className="inline-error" role="alert">{limitError}</p>}

      {generating && (
        <div className="progress" role="status">
          <Spinner small />
          <span>Generating shots… {data.total > 1 ? `scene ${Math.min(data.done + 1, data.total)} of ${data.total}` : ""}</span>
          <div className="progress-track"><div style={{ width: `${data.total ? (data.done / data.total) * 100 : 0}%` }} /></div>
        </div>
      )}
      {!generating && data.error && <p className="inline-error" role="alert">{data.error}</p>}

      {groups.map((g) => (
        <section key={g.number} className="scene-group" id={`shots-scene-${g.number}`}
          onDragOver={(e) => { if (drag !== null && g.inScript) { e.preventDefault(); if (!g.shots.length) setDropAt({ scene: g.number, index: 0 }); } }}
          onDrop={(e) => { e.preventDefault(); drop(); }}>
          <header className="scene-head">
            <span className="scene-num">{g.number}</span>
            <h3>{g.heading}</h3>
            {!g.inScript && <span className="pill warn">No longer in screenplay</span>}
            <span className="muted">{g.shots.length || "No"} {g.shots.length === 1 ? "shot" : "shots"}</span>
            <span className="spacer" />
            {g.inScript && (
              <>
                <button className="btn ghost small" disabled={generating}
                  onClick={() => (g.shots.length ? setConfirmScene(g) : generate(g.number))}>
                  <Icon name="refresh" size={14} /> {g.shots.length ? "Regenerate scene" : "Generate"}
                </button>
                <button className="btn ghost small" onClick={() => run(() => api.addShot(scriptId, g.number, null))}>
                  <Icon name="plus" size={14} /> Add shot
                </button>
              </>
            )}
          </header>

          {g.shots.length > 0 && (
            <div className="shot-table">
              <div className="shot-row head" aria-hidden="true">
                <span /><span>#</span><span>Size</span><span>Angle</span><span>Movement</span><span>Lens</span><span>Description</span><span />
              </div>
              {g.shots.map((s, i) => (
                <ShotRow key={s.id} shot={s} scriptId={scriptId} first={i === 0} last={i === g.shots.length - 1}
                  dragging={drag === s.id}
                  dropBefore={dropAt?.scene === g.number && dropAt.index === dropIndex(g.shots, drag, i, false) && drag !== s.id}
                  dropAfter={dropAt?.scene === g.number && i === g.shots.length - 1 && drag !== s.id && dropAt.index === dropIndex(g.shots, drag, i, true)}
                  onDragStart={() => setDrag(s.id)}
                  onDragEnd={() => { setDrag(null); setDropAt(null); }}
                  onDragOverRow={(after) => g.inScript && setDropAt({ scene: g.number, index: dropIndex(g.shots, drag, i, after) })}
                  onSaved={(shot) => { patchLocal(shot); setLimitError(""); }} onError={report} onUsageChanged={onUsageChanged}
                  onDuplicate={() => run(() => api.duplicateShot(scriptId, s.id))}
                  onDelete={() => run(() => api.deleteShot(scriptId, s.id))}
                  onMove={(delta) => move(s, g.number, i + delta)}
                />
              ))}
            </div>
          )}
        </section>
      ))}

      {confirmAll && (
        <Confirm title="Regenerate the whole shot list" confirmLabel="Regenerate all" danger onClose={() => setConfirmAll(false)}
          body={<p>This replaces all {data.shots.length} shots, including your edits, with a fresh pass from the current screenplay. To redo just one scene or one shot, use its own regenerate button.</p>}
          onConfirm={() => generate(null)} />
      )}
      {confirmScene && (
        <Confirm title={`Regenerate scene ${confirmScene.number}`} confirmLabel="Regenerate scene" danger onClose={() => setConfirmScene(null)}
          body={<p>This replaces the {confirmScene.shots.length} {confirmScene.shots.length === 1 ? "shot" : "shots"} in this scene. Other scenes are left alone.</p>}
          onConfirm={() => generate(confirmScene.number)} />
      )}
    </div>
  );
}

/** Index (among the scene's shots, excluding the dragged one) to insert at. */
function dropIndex(shots: Shot[], dragId: number | null, i: number, after: boolean): number {
  const without = shots.filter((s) => s.id !== dragId);
  const at = without.findIndex((s) => s.id === shots[i].id);
  return (at < 0 ? i : at) + (after ? 1 : 0);
}
interface RowProps {
  shot: Shot; scriptId: string; first: boolean; last: boolean; dragging: boolean; dropBefore: boolean; dropAfter: boolean;
  onDragStart: () => void; onDragEnd: () => void; onDragOverRow: (after: boolean) => void;
  onSaved: (s: Shot) => void; onError: (e: unknown) => void; onUsageChanged: () => void; onDuplicate: () => void; onDelete: () => void; onMove: (delta: number) => void;
}

function ShotRow(p: RowProps) {
  const { shot } = p;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [instruction, setInstruction] = useState("");
  const row = useRef<HTMLDivElement>(null);

  const save = async (patch: Partial<Shot>) => {
    try { p.onSaved(await api.updateShot(p.scriptId, shot.id, patch)); } catch (e) { p.onError(e); }
  };
  const regenerate = async () => {
    setBusy(true);
    try {
      p.onSaved(await api.regenerateShot(p.scriptId, shot.id, instruction));
      setInstruction("");
    } catch (e) {
      p.onError(e);
    } finally {
      setBusy(false);
      p.onUsageChanged();
    }
  };

  return (
    <div ref={row}
      className={["shot-row", open && "open", p.dragging && "dragging", p.dropBefore && "drop-before", p.dropAfter && "drop-after", busy && "busy"].filter(Boolean).join(" ")}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        p.onDragOverRow(e.clientY > r.top + r.height / 2);
      }}>
      <span className="grip" draggable title="Drag to reorder" aria-hidden="true"
        onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(shot.id)); if (row.current) e.dataTransfer.setDragImage(row.current, 12, 12); p.onDragStart(); }}
        onDragEnd={p.onDragEnd}>
        <Icon name="grip" size={14} />
      </span>
      <span className="shot-id">{shot.scene_number}.{shot.position}</span>
      <select value={shot.size} title={SIZE_NAMES[shot.size]} aria-label="Shot size" onChange={(e) => save({ size: e.target.value })}>
        {SIZES.map((s) => <option key={s} value={s} title={SIZE_NAMES[s]}>{s}</option>)}
      </select>
      <Cell label="Angle" value={shot.angle} onSave={(v) => save({ angle: v })} list="angles" />
      <Cell label="Movement" value={shot.movement} onSave={(v) => save({ movement: v })} list="movements" />
      <Cell label="Lens" value={shot.lens} onSave={(v) => save({ lens: v })} mono />
      <Cell label="Description" value={shot.description} onSave={(v) => save({ description: v })} multiline placeholder="What do we see?" />
      <span className="shot-actions">
        {busy ? <Spinner small /> : (
          <button className="icon-btn" title="Regenerate this shot" aria-label="Regenerate this shot" onClick={regenerate}><Icon name="refresh" size={15} /></button>
        )}
        <button className="icon-btn" aria-expanded={open} aria-label={open ? "Hide details" : "Show details"} title="Details" onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? "up" : "down"} size={15} />
        </button>
        <Menu items={[
          { label: "Duplicate", onSelect: p.onDuplicate },
          { label: "Move up", onSelect: () => p.onMove(-1), disabled: p.first },
          { label: "Move down", onSelect: () => p.onMove(1), disabled: p.last },
          { label: "Delete", onSelect: p.onDelete, danger: true },
        ]} />
      </span>
      {open && (
        <div className="shot-detail">
          <Field label="Composition"><Cell label="Composition" value={shot.composition} onSave={(v) => save({ composition: v })} multiline placeholder="Framing and subject placement" /></Field>
          <Field label="Intent"><Cell label="Intent" value={shot.notes} onSave={(v) => save({ notes: v })} multiline placeholder="Why this shot serves the beat" /></Field>
          <Field label="Characters"><Cell label="Characters" value={shot.characters.join(", ")} onSave={(v) => save({ characters: split(v) })} placeholder="Comma-separated" /></Field>
          <Field label="Props & elements"><Cell label="Props" value={shot.props.join(", ")} onSave={(v) => save({ props: split(v) })} placeholder="Comma-separated" /></Field>
          <Field label="Regenerate with direction" wide>
            <form className="row" onSubmit={(e) => { e.preventDefault(); regenerate(); }}>
              <input className="cell" value={instruction} placeholder="e.g. handheld, closer, from John's point of view" onChange={(e) => setInstruction(e.target.value)} />
              <button className="btn small" disabled={busy}>{busy ? <Spinner small /> : "Regenerate shot"}</button>
            </form>
          </Field>
        </div>
      )}
    </div>
  );
}

const split = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

function Field({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return <div className={wide ? "field-block wide" : "field-block"}><span className="field-label">{label}</span>{children}</div>;
}

/** Inline-editable cell: commits on blur or Enter, reverts on Escape. */
function Cell({ label, value, onSave, multiline, mono, placeholder, list }: {
  label: string; value: string; onSave: (v: string) => void; multiline?: boolean; mono?: boolean; placeholder?: string; list?: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => { if (draft.trim() !== value) onSave(draft.trim()); };
  const common = {
    className: mono ? "cell mono" : "cell",
    value: draft,
    placeholder: placeholder ?? "—",
    "aria-label": label,
    onBlur: commit,
  };
  if (multiline)
    return <AutoTextarea {...common} onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Escape") { setDraft(value); e.currentTarget.blur(); } if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.blur(); }} />;
  return <input {...common} list={list} onChange={(e) => setDraft(e.target.value)}
    onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { setDraft(value); } }} />;
}

export function ShotDatalists() {
  return (
    <>
      <datalist id="angles">
        {["Eye level", "Low angle", "High angle", "Over-the-shoulder", "POV", "Overhead", "Dutch angle", "Profile", "Two-shot"].map((a) => <option key={a} value={a} />)}
      </datalist>
      <datalist id="movements">
        {["Static", "Slow push-in", "Pull-out", "Pan", "Tilt", "Tracking", "Handheld", "Steadicam", "Dolly", "Crane", "Rack focus"].map((a) => <option key={a} value={a} />)}
      </datalist>
    </>
  );
}
