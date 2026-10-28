// The console's only door to memory data. Pages and server actions import from
// here and never from a backend directly.
//
// With MEMVINE_API_URL set, calls go to the Memvine server (api-live.ts).
// Without it, an in-memory mock seeded from mock-data.ts stands in, so the UI
// can be worked on without a database.
import "server-only";
import { cookies } from "next/headers";
import type { MemvineApi, Project } from "./types";

export const isLive = Boolean(process.env.MEMVINE_API_URL);

export interface ViewerSummary {
  label: string;
  detail: string | null;
  canSignOut: boolean;
  canManage: boolean;
}

/** Who the console acts for: a signed-in person, an organization key, the operator, or null in mock mode. */
export async function viewer(): Promise<ViewerSummary | null> {
  if (!isLive) return null;
  const live = await import("./api-live");
  const who = await live.whoIs(await live.managementKey());
  const canSignOut = !process.env.MEMVINE_MANAGEMENT_KEY;
  if (!who) return { label: "unknown key", detail: null, canSignOut, canManage: false };
  if (who.operator) return { label: "Operator (all organizations)", detail: null, canSignOut, canManage: true };
  return {
    label: who.user ? who.user.email : who.org?.name ?? "organization",
    detail: who.user ? `${who.org?.name ?? ""} · ${who.role}` : "organization key",
    canSignOut,
    canManage: who.role === "owner" || who.role === "admin",
  };
}

/** The API address agents use: MEMVINE_PUBLIC_API_URL when the console reaches the API at a
 *  private address (a container name, localhost behind a tunnel), else the console's own. */
export function publicApiUrl(): string {
  return (process.env.MEMVINE_PUBLIC_API_URL || process.env.MEMVINE_API_URL || "https://api.memvine.dev").replace(/\/$/, "");
}

/** The signed-in person's email, to fill in connect commands; a placeholder otherwise. */
export async function viewerEmail(): Promise<string> {
  if (!isLive) return "you@company.com";
  const live = await import("./api-live");
  return (await live.whoIs(await live.managementKey()))?.user?.email ?? "you@company.com";
}

/** Owners, admins, organization keys and the operator may change the organization; members may not. */
export async function canManage(): Promise<boolean> {
  return (await viewer())?.canManage ?? true;
}

async function backend(): Promise<MemvineApi> {
  return isLive ? (await import("./api-live")).liveApi : (await import("./api-mock")).mockApi;
}

export const PROJECT_COOKIE = "mv_project";

export async function listProjects(): Promise<Project[]> {
  return (await backend()).listProjects();
}

/** The project selected in the header switcher; the first project when none is set. */
export async function currentProject(): Promise<Project> {
  const projects = await listProjects();
  if (projects.length === 0) {
    // A new organization starts empty; its first project is created on the Projects page.
    return { slug: "", name: "No project yet", createdAt: new Date().toISOString(), memoryCount: 0, tags: [], sourceCount: 0,
             keyCount: 0, lastActivityAt: null, monthlyCreditCap: null, retentionDays: null, monthCredits: 0 };
  }
  const slug = (await cookies()).get(PROJECT_COOKIE)?.value;
  return projects.find((p) => p.slug === slug) ?? projects[0];
}

export async function selectProject(slug: string): Promise<void> {
  if (!(await listProjects()).some((p) => p.slug === slug)) throw new Error(`unknown project: ${slug}`);
  (await cookies()).set(PROJECT_COOKIE, slug, { path: "/", sameSite: "lax" });
}

type Fn<K extends keyof MemvineApi> = MemvineApi[K];
const forward = <K extends keyof MemvineApi>(name: K, whenNoProject?: unknown): Fn<K> =>
  (async (...args: unknown[]) => {
    // An organization with no project yet: project-scoped reads are empty rather than errors
    // (pages render alongside the layout's first-project prompt).
    if (whenNoProject !== undefined && args[0] === "") return whenNoProject;
    const api = await backend();
    return (api[name] as (...a: unknown[]) => unknown)(...args);
  }) as Fn<K>;

export const createProject = forward("createProject");
export const updateProject = forward("updateProject");
export const deleteProject = forward("deleteProject");
export const listKeys = forward("listKeys", []);
export const createKey = forward("createKey");
export const revokeKey = forward("revokeKey");
export const listMemories = forward("listMemories", []);
export const listThreads = forward("listThreads", []);
export const getThread = forward("getThread", null);
export const listSessions = forward("listSessions", []);
export const deleteSession = forward("deleteSession");
export const listAudiences = forward("listAudiences", []);
export const putAudience = forward("putAudience");
export const deleteAudience = forward("deleteAudience");
export const listIdentities = forward("listIdentities", []);
export const putIdentity = forward("putIdentity");
export const getMemory = forward("getMemory", null);
export const setMemoryStatus = forward("setMemoryStatus");
export const getRelated = forward("getRelated", null);
export const getGraph = forward("getGraph", { nodes: [], edges: [], since: null, truncated: false });
export const correctMemory = forward("correctMemory");
export const confirmMemory = forward("confirmMemory");
export const listEpisodes = forward("listEpisodes", []);
export const getEpisode = forward("getEpisode", null);
export const retryJob = forward("retryJob");
export const listRequests = forward("listRequests", []);
export const getUsage = forward("getUsage", []);
export const listMembers = forward("listMembers");
export const orgInfo = forward("orgInfo");
export const changeRole = forward("changeRole");
export const removeMember = forward("removeMember");
export const listInvites = forward("listInvites");
export const createInvite = forward("createInvite");
export const revokeInvite = forward("revokeInvite");
export const changePassword = forward("changePassword");
export const switchOrg = forward("switchOrg");
export const listAudit = forward("listAudit");
export const listOrgKeys = forward("listOrgKeys");
export const createOrgKey = forward("createOrgKey");
export const revokeOrgKey = forward("revokeOrgKey");
export const listOrgs = forward("listOrgs");
export const createOrg = forward("createOrg");
export const recall = forward("recall");
export const loadSample = forward("loadSample");
export const deleteSample = forward("deleteSample");
export const addNow = forward("addNow");
