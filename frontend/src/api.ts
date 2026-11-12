import type { AdminOverview, AdminUser, Channel, LegalDoc, Message, Proposal, Revision, Scene, Script, Shot, ShotList, Usage, User } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      credentials: "same-origin",
      ...init,
      headers: init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 401 && path !== "/me") window.dispatchEvent(new Event("pys:unauthorized"));
  if (!res.ok) {
    let detail = "Something went wrong. Please try again.";
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = body.detail[0].msg;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: JSON.stringify(body ?? {}) });

export const api = {
  authConfig: () => req<{ google: boolean; dev_login: boolean }>("/auth/config"),
  me: () => req<User>("/me"),
  usage: () => req<Usage>("/me/usage"),
  deleteAccount: () => req<void>("/me", json("DELETE", { confirm: "DELETE" })),
  exportUrl: "/api/me/export",
  legal: (name: "privacy" | "terms") => req<LegalDoc>(`/legal/${name}`),

  adminOverview: () => req<AdminOverview>("/admin/overview"),
  adminUsers: (q: string) => req<{ users: AdminUser[] }>(`/admin/users?q=${encodeURIComponent(q)}`),
  adminSetDisabled: (id: string, disabled: boolean) => req<AdminUser>(`/admin/users/${id}/disabled`, json("POST", { disabled })),
  devLogin: (email: string) => req<User>("/auth/dev-login", json("POST", { email, name: email.split("@")[0] })),
  logout: () => req<void>("/auth/logout", json("POST")),

  scripts: () => req<{ scripts: Script[]; limit: number }>("/scripts"),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Script>("/scripts", { method: "POST", body: fd });
  },
  script: (id: string) => req<Script>(`/scripts/${id}`),
  rename: (id: string, title: string) => req<Script>(`/scripts/${id}`, json("PATCH", { title })),
  remove: (id: string) => req<void>(`/scripts/${id}`, { method: "DELETE" }),
  reprocess: (id: string) => req<Script>(`/scripts/${id}/reprocess`, json("POST")),

  revisions: (id: string) => req<{ revisions: Revision[]; current_revision_id: number }>(`/scripts/${id}/revisions`),
  revision: (id: string, rid: number) => req<Revision & { content: string }>(`/scripts/${id}/revisions/${rid}`),
  restore: (id: string, rid: number) => req<Script>(`/scripts/${id}/revisions/${rid}/restore`, json("POST")),
  saveContent: (id: string, content: string) => req<Script>(`/scripts/${id}/content`, json("PUT", { content })),

  messages: (id: string, channel: Channel) => req<{ messages: Message[] }>(`/scripts/${id}/messages?channel=${channel}`),
  clearMessages: (id: string, channel: Channel) => req<void>(`/scripts/${id}/messages?channel=${channel}`, { method: "DELETE" }),
  chat: (id: string, message: string, mode: "discuss" | "edit") =>
    req<{ messages: Message[] }>(`/scripts/${id}/chat`, json("POST", { message, mode })),
  applyProposal: (id: string, pid: number, indices: number[]) =>
    req<{ proposal: Proposal; revision: Revision & { content: string }; scenes: Scene[] }>(
      `/scripts/${id}/proposals/${pid}/apply`, json("POST", { indices }),
    ),
  rejectProposal: (id: string, pid: number) => req<{ proposal: Proposal }>(`/scripts/${id}/proposals/${pid}/reject`, json("POST")),

  shots: (id: string) => req<ShotList>(`/scripts/${id}/shots`),
  generateShots: (id: string, scene_number: number | null) => req<ShotList>(`/scripts/${id}/shots/generate`, json("POST", { scene_number })),
  addShot: (id: string, scene_number: number, index: number | null) =>
    req<ShotList>(`/scripts/${id}/shots`, json("POST", { scene_number, index })),
  updateShot: (id: string, sid: number, patch: Partial<Shot>) => req<Shot>(`/scripts/${id}/shots/${sid}`, json("PATCH", patch)),
  deleteShot: (id: string, sid: number) => req<ShotList>(`/scripts/${id}/shots/${sid}`, { method: "DELETE" }),
  duplicateShot: (id: string, sid: number) => req<ShotList>(`/scripts/${id}/shots/${sid}/duplicate`, json("POST")),
  moveShot: (id: string, sid: number, scene_number: number, index: number) =>
    req<ShotList>(`/scripts/${id}/shots/${sid}/move`, json("PUT", { scene_number, index })),
  fillMissing: (id: string) =>
    req<ShotList & { filled: { shots: number; repaired: number; fallback: number } }>(`/scripts/${id}/shots/fill-missing`, json("POST")),
  regenerateShot: (id: string, sid: number, instruction: string) =>
    req<Shot>(`/scripts/${id}/shots/${sid}/regenerate`, json("POST", { instruction })),
  shotChat: (id: string, message: string) =>
    req<ShotList & { messages: Message[]; changes: { added: number; updated: number; deleted: number } }>(
      `/scripts/${id}/shots/chat`, json("POST", { message }),
    ),
};

export const errorText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong.");

/** A plan limit (429) or a feature the plan doesn't include (403): shown inline, next to the action. */
export const isLimitError = (e: unknown) => e instanceof ApiError && (e.status === 429 || e.status === 403);
