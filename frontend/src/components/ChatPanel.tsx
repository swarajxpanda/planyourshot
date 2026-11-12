import { useCallback, useEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import { api, errorText } from "../api";
import type { Channel, Edit, Message, Proposal, Revision, Scene, ShotList, Usage } from "../types";
import { AutoTextarea, Icon, Menu, Spinner } from "./ui";

interface Props {
  scriptId: string;
  channel: Channel;
  hasShots: boolean;
  generating: boolean;
  onScreenplayChanged: (revision: Revision & { content: string }, scenes: Scene[]) => void;
  onShotsChanged: (list: ShotList) => void;
  onClose: () => void;
  usage: Usage | null;
  onUsageChanged: () => void;
}

/** Shown under the composer only once more than 60% of the message allowance is used. */
function usageNote(usage: Usage | null): string {
  if (!usage) return "";
  const used = usage.usage.messages, limit = usage.limits.messages_per_day;
  return limit > 0 && used / limit > 0.6 ? `${Math.min(used, limit)} of ${limit} messages today` : "";
}

const STARTERS: Record<string, string[]> = {
  discuss: ["Summarise the story in five beats", "Which scene has the weakest dialogue, and why?", "Where does the pacing drag?"],
  edit: ["Tighten the dialogue in scene 1", "Make the opening image more visual", "Cut any line that states the subtext out loud"],
  shots: ["Add an establishing shot to every exterior scene", "Make scene 1 feel more claustrophobic", "Which scenes are over-covered?"],
};

export default function ChatPanel({ scriptId, channel, hasShots, generating, onScreenplayChanged, onShotsChanged, onClose, usage, onUsageChanged }: Props) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [mode, setMode] = useState<"discuss" | "edit">("discuss");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState<string | null>(null); // the message in flight
  const [error, setError] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setMessages(null);
    setError("");
    api.messages(scriptId, channel).then((r) => setMessages(r.messages)).catch((e) => { setMessages([]); setError(errorText(e)); });
  }, [scriptId, channel]);
  useEffect(load, [load]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages, sending]);

  const send = async (text = draft) => {
    text = text.trim();
    if (!text || sending) return;
    setSending(text);
    setError("");
    setDraft("");
    try {
      if (channel === "script") {
        const r = await api.chat(scriptId, text, mode);
        setMessages((m) => [...(m ?? []), ...r.messages]);
      } else {
        const r = await api.shotChat(scriptId, text);
        setMessages((m) => [...(m ?? []), ...r.messages]);
        onShotsChanged({ shots: r.shots, status: r.status, error: r.error, done: r.done, total: r.total });
      }
    } catch (e) {
      setError(errorText(e));
      setDraft(text); // nothing was saved; let them retry
    } finally {
      setSending(null);
      onUsageChanged();
    }
  };

  const updateProposal = (p: Proposal) =>
    setMessages((m) => (m ?? []).map((msg) => (msg.proposal?.id === p.id ? { ...msg, proposal: p } : msg)));

  const kind = channel === "shots" ? "shots" : mode;
  const blocked = channel === "shots" && generating;
  const placeholder = channel === "shots"
    ? (hasShots ? "Ask for changes to the shot list…" : "Ask about coverage, or generate a shot list first…")
    : mode === "edit" ? "Describe the change you want…" : "Ask about the screenplay…";

  return (
    <aside className="assistant" id="assistant-panel" aria-label="Assistant">
      <div className="assistant-head">
        <div>
          <h2>Assistant</h2>
          <span className="muted">{channel === "shots" ? "Shot list" : "Screenplay"}</span>
        </div>
        {channel === "script" && (
          <div className="segmented" role="tablist" aria-label="Assistant mode">
            <button role="tab" aria-selected={mode === "discuss"} onClick={() => setMode("discuss")} title="Ask questions and get notes. Nothing is changed.">Discuss</button>
            <button role="tab" aria-selected={mode === "edit"} onClick={() => setMode("edit")} title="Get changes prepared for your approval.">Edit</button>
          </div>
        )}
        <Menu items={[{
          label: "Clear conversation", disabled: !messages?.length, danger: true,
          onSelect: () => api.clearMessages(scriptId, channel).then(load).catch((e) => setError(errorText(e))),
        }]} />
        <button className="icon-btn only-narrow" aria-label="Close assistant" onClick={onClose}><Icon name="x" /></button>
      </div>

      <div className="assistant-scroll" ref={scroller}>
        {messages === null && <div className="assistant-empty"><Spinner /></div>}
        {messages?.length === 0 && !sending && (
          <div className="assistant-empty">
            <p>
              {channel === "shots"
                ? "Revise the shot list in plain language. Changes are applied straight to the list."
                : mode === "edit"
                  ? "Describe a change. You'll review every edit before anything touches the screenplay."
                  : "Ask anything about the story, characters or scenes. This mode never changes the screenplay."}
            </p>
            <div className="starters">
              {STARTERS[kind].map((s) => <button key={s} disabled={blocked} onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {messages?.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            {m.role === "user" && channel === "script" && m.mode === "edit" && <span className="msg-tag">Edit request</span>}
            {m.role === "assistant" ? <MessageMarkdown text={m.content} /> : <div className="msg-text">{m.content}</div>}
            {m.proposal && (
              <ProposalCard scriptId={scriptId} proposal={m.proposal} onChange={updateProposal} onApplied={onScreenplayChanged} />
            )}
          </div>
        ))}
        {sending && (
          <>
            <div className="msg user"><div className="msg-text">{sending}</div></div>
            <div className="msg assistant pending"><Spinner small /> {kind === "discuss" ? "Reading the screenplay…" : kind === "edit" ? "Preparing changes…" : "Revising the shot list…"}</div>
          </>
        )}
      </div>

      {error && <p className="inline-error assistant-error" role="alert">{error}</p>}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <AutoTextarea value={draft} maxRows={6} placeholder={placeholder} disabled={blocked} aria-label="Message"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="btn primary icon-only" disabled={!draft.trim() || !!sending || blocked} aria-label="Send">
          <Icon name="send" />
        </button>
      </form>
      {usageNote(usage) && <p className="composer-note">{usageNote(usage)}</p>}
    </aside>
  );
}

// Headings in a chat reply read as small bold lead-ins, not document titles.
const Heading: Components["h1"] = ({ node: _node, ...props }) => <p className="md-heading" {...props} />;
const MD_COMPONENTS: Components = {
  h1: Heading, h2: Heading, h3: Heading, h4: Heading, h5: Heading, h6: Heading,
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

/** Assistant replies are markdown. Raw HTML is dropped (skipHtml) and images are not loaded, so model output can't inject markup. */
function MessageMarkdown({ text }: { text: string }) {
  return (
    <div className="msg-md">
      <Markdown skipHtml disallowedElements={["img"]} components={MD_COMPONENTS}>{text}</Markdown>
    </div>
  );
}

function ProposalCard({ scriptId, proposal, onChange, onApplied }: {
  scriptId: string; proposal: Proposal; onChange: (p: Proposal) => void;
  onApplied: (revision: Revision & { content: string }, scenes: Scene[]) => void;
}) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set(proposal.edits.map((_, i) => i)));
  const [busy, setBusy] = useState<"apply" | "reject" | null>(null);
  const [error, setError] = useState("");
  const pending = proposal.status === "pending";

  const toggle = (i: number) => setSelected((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });

  const apply = async () => {
    setBusy("apply");
    setError("");
    try {
      const r = await api.applyProposal(scriptId, proposal.id, [...selected].sort((a, b) => a - b));
      onChange(r.proposal);
      onApplied(r.revision, r.scenes);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const reject = async () => {
    setBusy("reject");
    try {
      onChange((await api.rejectProposal(scriptId, proposal.id)).proposal);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const applied = proposal.edits.filter((e) => e.status === "applied").length;
  return (
    <div className={`proposal ${proposal.status}`}>
      <div className="proposal-head">
        <span>{proposal.edits.length} suggested {proposal.edits.length === 1 ? "change" : "changes"}</span>
        {proposal.status === "applied" && <span className="pill ok"><Icon name="check" size={12} /> {applied} applied</span>}
        {proposal.status === "rejected" && <span className="pill">Dismissed</span>}
      </div>
      {proposal.edits.map((e, i) => <EditDiff key={i} edit={e} selectable={pending && proposal.edits.length > 1}
        checked={selected.has(i)} onToggle={() => toggle(i)} />)}
      {error && <p className="inline-error">{error}</p>}
      {pending && (
        <div className="proposal-foot">
          <button className="btn ghost small" disabled={!!busy} onClick={reject}>Dismiss</button>
          <button className="btn primary small" disabled={!!busy || selected.size === 0} onClick={apply}>
            {busy === "apply" ? <Spinner small /> : <Icon name="check" size={14} />}
            {selected.size === proposal.edits.length ? "Apply to screenplay" : `Apply ${selected.size} selected`}
          </button>
        </div>
      )}
    </div>
  );
}

function EditDiff({ edit, selectable, checked, onToggle }: { edit: Edit; selectable: boolean; checked: boolean; onToggle: () => void }) {
  return (
    <div className={`edit ${edit.status}`}>
      <label className="edit-reason">
        {selectable && <input type="checkbox" checked={checked} onChange={onToggle} />}
        <span>{edit.reason || "Change"}</span>
        {edit.status === "failed" && <span className="pill warn">Text changed — not applied</span>}
        {edit.status === "rejected" && <span className="pill">Skipped</span>}
      </label>
      <pre className="diff del">{edit.find}</pre>
      {edit.replace.trim() ? <pre className="diff add">{edit.replace}</pre> : <p className="diff-note">Removed</p>}
    </div>
  );
}
