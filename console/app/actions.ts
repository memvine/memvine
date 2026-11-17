"use server";
// Server actions for every write the console makes. Each resolves the current
// project itself, so a stale form can never act on another project.
import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import * as api from "@/lib/api";
import { BUILTIN_CATEGORIES, INSTANT_MAX_CHARS, MAX_MEMORY_INSTRUCTIONS, MAX_PROJECT_CATEGORIES } from "@/lib/types";

export interface SignInState {
  error?: string;
  ok?: boolean;
  /** Where to go instead, e.g. the sign-up page for an invite pasted into a sign-in field. */
  goTo?: string;
  /** What was typed (never passwords), so a refused form keeps it: React resets a form after its action. */
  values?: Record<string, string>;
}

/** Keeps the session token (mv_us_) or organization key (mv_mk_) in an httpOnly cookie. */
async function keepSession(token: string) {
  const { KEY_COOKIE } = await import("@/lib/api-live");
  const jar = await cookies();
  jar.set(KEY_COOKIE, token, { httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production" && !process.env.MEMVINE_INSECURE_COOKIES, path: "/", maxAge: 60 * 60 * 24 * 30 });
  jar.delete("mv_project");
}

/** The invite code in a pasted invite link or code, if that is what was pasted. */
function inviteIn(text: string): string | null {
  const match = text.match(/mv_in_[A-Za-z0-9_-]+/);
  return match ? match[0] : null;
}

export async function signIn(_prev: SignInState, formData: FormData): Promise<SignInState> {
  const { whoIs, publicCall } = await import("@/lib/api-live");
  const key = String(formData.get("key") ?? "").trim();
  // An invite link pasted into any sign-in field: take the person to sign-up with it.
  const pasted = inviteIn(`${key} ${formData.get("email") ?? ""} ${formData.get("invite") ?? ""}`);
  if (pasted) return { goTo: `/signup?invite=${pasted}` };
  // A project's agent key pasted into any field (a common mix-up): say what it is and what signs in instead.
  const typed = ["key", "email", "password", "invite"].map((f) => String(formData.get(f) ?? "")).join(" ");
  if (typed.includes("mv_sk_")) {
    return {
      error: "That is an agent API key (mv_sk_…). Agent keys are created inside the Console, under API keys, and let an agent read and write one project's memory; they never sign in to the Console. Sign in with your email and password, an invite link, or an organization key (mv_mk_…).",
      values: { email: String(formData.get("email") ?? "").includes("mv_sk_") ? "" : String(formData.get("email") ?? "") },
    };
  }
  if (formData.has("invite")) return { error: "That is not an invite link. Invite links contain /signup?invite=mv_in_…" };
  if (key) {
    // Organization keys still sign in, for the operator and for shared admin access.
    if (!key.startsWith("mv_mk_")) {
      return { error: "Organization keys start with mv_mk_. To join with an invite link, open the link itself, or choose \"I have an invite link\" below." };
    }
    if (!(await whoIs(key))) return { error: "That key was not accepted. Check it, or ask an admin for a new one." };
    await keepSession(key);
    // The client does a full page load: the app shell differs from the sign-in page's.
    return { ok: true };
  }
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password.", values: { email } };
  const r = await publicCall<{ token: string }>("/v1/auth/login", { email, password });
  if (!r.ok) return { error: r.message, values: { email } };
  await keepSession(r.data.token);
  return { ok: true };
}

export async function signUp(_prev: SignInState, formData: FormData): Promise<SignInState> {
  const { publicCall } = await import("@/lib/api-live");
  const field = (name: string) => String(formData.get(name) ?? "").trim();
  const values = { name: field("name"), email: field("email"), organization: field("organization") };
  const password = String(formData.get("password") ?? "");
  if (password !== String(formData.get("confirm") ?? "")) return { error: "The two passwords differ.", values };
  const r = await publicCall<{ token: string }>("/v1/auth/signup", {
    email: values.email, password, name: values.name, organization: values.organization, invite: field("invite") || null,
  });
  if (!r.ok) return { error: r.message, values };
  await keepSession(r.data.token);
  return { ok: true };
}

/** A signed-in person joins another organization from an invite link. */
export async function acceptInvite(_prev: SignInState, formData: FormData): Promise<SignInState> {
  const { publicCall, managementKey } = await import("@/lib/api-live");
  const r = await publicCall<{ token: string }>("/v1/auth/accept", { invite: String(formData.get("invite") ?? "") }, await managementKey());
  if (!r.ok) return { error: r.message };
  await keepSession(r.data.token);
  return { ok: true };
}

export async function switchOrg(formData: FormData) {
  const token = await api.switchOrg(String(formData.get("org")));
  if (token) await keepSession(token);
  redirect("/");
}

/** Where people reach this console: MEMVINE_PUBLIC_CONSOLE_URL when set (the laptop server sets
 *  it while shared), so an invite made while browsing at localhost still works for others; else
 *  the address this request came in on. */
async function consoleOrigin(): Promise<string> {
  const configured = (process.env.MEMVINE_PUBLIC_CONSOLE_URL ?? "").replace(/\/$/, "");
  if (configured) return configured;
  const { headers } = await import("next/headers");
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (/^(localhost|127\.)/.test(host) ? "http" : "https");
  return `${proto}://${host}`;
}

export interface InviteState {
  link?: string;
  error?: string;
}

export async function createInvite(_prev: InviteState, formData: FormData): Promise<InviteState> {
  const email = String(formData.get("email") ?? "").trim() || null;
  const role = String(formData.get("role") ?? "member") as "admin" | "member";
  try {
    const { token } = await api.createInvite(email, role);
    revalidatePath("/settings");
    return { link: `${await consoleOrigin()}/signup?invite=${token}` };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not create the invite." };
  }
}

export async function revokeInvite(formData: FormData) {
  await api.revokeInvite(String(formData.get("id")));
  revalidatePath("/settings");
}

export async function changeRole(formData: FormData) {
  await api.changeRole(String(formData.get("id")), String(formData.get("role")) as "owner" | "admin" | "member");
  revalidatePath("/settings");
}

export async function removeMember(formData: FormData) {
  await api.removeMember(String(formData.get("id")));
  revalidatePath("/settings");
}

export interface PasswordState {
  ok?: boolean;
  error?: string;
}

export async function changePassword(_prev: PasswordState, formData: FormData): Promise<PasswordState> {
  const next = String(formData.get("new") ?? "");
  if (next !== String(formData.get("confirm") ?? "")) return { error: "The two new passwords differ." };
  try {
    await api.changePassword(String(formData.get("current") ?? ""), next);
    return { ok: true };
  } catch (e) {
    return { error: e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : "Could not change the password." };
  }
}

export async function switchProject(formData: FormData) {
  await api.selectProject(String(formData.get("project")));
  revalidatePath("/", "layout");
}

export interface CreateKeyState {
  secret?: string;
  name?: string;
  /** The new key's id, so the connect page can watch for its first use. */
  id?: string;
  error?: string;
}

export async function createKey(_prev: CreateKeyState, formData: FormData): Promise<CreateKeyState> {
  const name = String(formData.get("name") ?? "").trim();
  if (!name) return { error: "Give the key a name, such as production or laptop." };
  if (name.length > 60) return { error: "Keep the name under 60 characters." };
  const project = await api.currentProject();
  try {
    const { key, secret } = await api.createKey(project.slug, name);
    revalidatePath("/keys");
    return { secret, name, id: key.id };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not create the key." };
  }
}

/** The connect page's "Check connection" poll: whether an agent has used this key of the current project yet. */
export async function checkKeyConnection(id: string) {
  const project = await api.currentProject();
  return project.slug ? api.keyConnection(project.slug, id) : null;
}

export async function revokeKey(formData: FormData) {
  const project = await api.currentProject();
  await api.revokeKey(project.slug, String(formData.get("id")));
  revalidatePath("/keys");
}

/** Where a memory action returns to: the page it came from (a drawer on a list) when the form
 *  names one inside the console, else the memory's own page. */
function memoryReturn(formData: FormData, id: string): string {
  const next = String(formData.get("next") ?? "");
  return /^\/(memories|knowledge)(\?|$)/.test(next) ? next : `/memories/${id}`;
}

export async function setMemoryStatus(formData: FormData) {
  const project = await api.currentProject();
  const id = String(formData.get("id"));
  const status = formData.get("status") === "retired" ? "retired" : "active";
  await api.setMemoryStatus(project.slug, id, status);
  revalidatePath("/memories");
  revalidatePath("/knowledge");
  redirect(memoryReturn(formData, id));
}

export async function confirmMemory(formData: FormData) {
  const project = await api.currentProject();
  const id = String(formData.get("id"));
  await api.confirmMemory(project.slug, id);
  revalidatePath("/memories");
  revalidatePath("/knowledge");
  redirect(memoryReturn(formData, id));
}

// ---- the receipt's actions: they act in place, wherever the receipt is shown, and refresh every page.

export interface ReceiptState {
  error?: string;
  /** After Share or Take back: what happened in the team space, in a sentence. */
  note?: string;
  /** The fact that now holds, after Wrong: the receipt's page can follow it. */
  replacedBy?: string;
}

const plainError = (e: unknown, fallback: string) => (e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : fallback);

/** Still true: the person confirms it, which re-anchors it to the code as it is now. */
export async function stillTrue(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const project = await api.currentProject();
  try {
    await api.confirmMemory(project.slug, String(formData.get("id")));
  } catch (e) {
    return { error: plainError(e, "Could not confirm it.") };
  }
  revalidatePath("/", "layout");
  return {};
}

/** Share: copy this memory to a team space, with who said it and the quoted line it rests on. */
export async function shareMemory(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const project = await api.currentProject();
  const team = String(formData.get("team") ?? "");
  if (!team) return { error: "Choose a team space." };
  let relation: string;
  try {
    ({ relation } = await api.shareMemory(project.slug, String(formData.get("id")), team));
  } catch (e) {
    return { error: plainError(e, "Could not share it.") };
  }
  revalidatePath("/", "layout");
  return { note: relation === "same" ? "The team already had this; yours is added as support."
    : relation === "update" ? "Shared. It replaced an older value in the team's memory." : "Shared with the team." };
}

/** Take back: remove this memory from the team space (it stays when a teammate also shared it). */
export async function unshareMemory(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const project = await api.currentProject();
  let takenBack: boolean;
  try {
    ({ takenBack } = await api.unshareMemory(project.slug, String(formData.get("id"))));
  } catch (e) {
    return { error: plainError(e, "Could not take it back.") };
  }
  revalidatePath("/", "layout");
  return { note: takenBack ? "Taken back from the team." : "A teammate shared this too, so the team keeps it; yours is private again." };
}

/** Dismiss: what background consolidation flagged is fine as it is; the flag goes, the memory stays. */
export async function dismissFlag(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const project = await api.currentProject();
  try {
    await api.dismissFlag(project.slug, String(formData.get("id")));
  } catch (e) {
    return { error: plainError(e, "Could not dismiss it.") };
  }
  revalidatePath("/", "layout");
  return {};
}

/** Forget: retire the fact (or, with `restore`, bring a forgotten one back). */
export async function forgetMemory(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const project = await api.currentProject();
  try {
    await api.setMemoryStatus(project.slug, String(formData.get("id")), formData.get("restore") ? "active" : "retired");
  } catch (e) {
    return { error: plainError(e, "Could not forget it.") };
  }
  revalidatePath("/", "layout");
  return {};
}

/** Wrong: store what is true instead (POST /v1/memories), so it supersedes the fact. */
export async function correctMemory(_prev: ReceiptState, formData: FormData): Promise<ReceiptState> {
  const statement = String(formData.get("statement") ?? "").trim();
  if (statement.length < 3) return { error: "Say what is true instead, in a sentence." };
  if (statement.length > 2000) return { error: "Keep it under 2,000 characters." };
  const project = await api.currentProject();
  try {
    const next = await api.correctMemory(project.slug, String(formData.get("id")), statement);
    revalidatePath("/", "layout");
    return { replacedBy: next.id };
  } catch (e) {
    return { error: plainError(e, "Could not store the correction.") };
  }
}

export interface SampleState {
  error?: string;
}

/** Fill the current project with sample memory: instant, no model call, no credits. */
export async function loadSampleData(_prev: SampleState, _formData: FormData): Promise<SampleState> {
  const project = await api.currentProject();
  try {
    await api.loadSample(project.slug);
  } catch (e) {
    return { error: e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : "Could not load the sample data." };
  }
  revalidatePath("/", "layout");
  return {};
}

/** Delete the sample sources of the current project, and the memories drawn from them. */
export async function deleteSampleData(_prev: SampleState, _formData: FormData): Promise<SampleState> {
  const project = await api.currentProject();
  try {
    await api.deleteSample(project.slug);
  } catch (e) {
    return { error: e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : "Could not delete the sample data." };
  }
  revalidatePath("/", "layout");
  return {};
}

export async function retryJob(formData: FormData) {
  const project = await api.currentProject();
  await api.retryJob(project.slug, Number(formData.get("jobId")));
  revalidatePath("/sources");
}

const parseTags = (raw: FormDataEntryValue | null) =>
  String(raw ?? "").split(/[\s,]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);

const TAG_RE = /^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$/;

export interface ProjectFormState {
  error?: string;
  created?: { slug: string; name: string; secret: string; teamSpace?: boolean };
  saved?: boolean;
  deleted?: string;
}

export async function createProject(_prev: ProjectFormState, formData: FormData): Promise<ProjectFormState> {
  const name = String(formData.get("name") ?? "").trim();
  const tags = parseTags(formData.get("tags"));
  const description = String(formData.get("description") ?? "").trim();
  if (!name) return { error: "Give the project a name." };
  if (name.length > 100) return { error: "Keep the name under 100 characters." };
  if (tags.some((t) => !TAG_RE.test(t))) return { error: "Tags are lowercase letters, digits and dashes." };
  if (description.length > 2000) return { error: "Keep the description under 2,000 characters." };
  try {
    // A team space holds what people share; sample sources would be conversation in it.
    const kind = String(formData.get("kind") ?? "shared");
    const teamSpace = kind === "team";
    const { project, secret } = await api.createProject(name, tags, { description: description || null,
                                                                     sample: !teamSpace && formData.get("sample") === "on", teamSpace,
                                                                     personal: kind === "personal" });
    revalidatePath("/", "layout");
    return { created: { slug: project.slug, name: project.name, secret, teamSpace } };
  } catch (e) {
    const message = e instanceof Error ? e.message : "Could not create the project.";
    return { error: message.includes("slug-taken") ? "A project with that name already exists." : message };
  }
}

export async function updateProject(_prev: ProjectFormState, formData: FormData): Promise<ProjectFormState> {
  const slug = String(formData.get("slug"));
  const name = String(formData.get("name") ?? "").trim();
  const tags = parseTags(formData.get("tags"));
  const capRaw = String(formData.get("cap") ?? "").trim();
  const retentionRaw = String(formData.get("retention") ?? "").trim();
  if (!name) return { error: "The name cannot be empty." };
  if (tags.some((t) => !TAG_RE.test(t))) return { error: "Tags are lowercase letters, digits and dashes." };
  const relevanceRaw = String(formData.get("minRelevance") ?? "").trim();
  const minRelevance = relevanceRaw === "" ? null : Number(relevanceRaw);
  if (minRelevance !== null && !(minRelevance >= 0 && minRelevance <= 1)) {
    return { error: "The recall threshold is a number from 0 to 1, or empty for the server default." };
  }
  const halfLifeRaw = String(formData.get("halfLife") ?? "").trim();
  const halfLife = halfLifeRaw === "" ? null : Number(halfLifeRaw);
  if (halfLife !== null && !(halfLife >= 0 && halfLife <= 36500)) {
    return { error: "The recency half-life is a number of days (0 turns it off), or empty for the server default." };
  }
  const cap = capRaw === "" ? null : Number(capRaw);
  const retention = retentionRaw === "" ? null : Number(retentionRaw);
  if (cap !== null && !(cap >= 0)) return { error: "The cap is a number of units, or empty for uncapped." };
  if (retention !== null && !(Number.isInteger(retention) && retention >= 1 && retention <= 3650)) {
    return { error: "Retention is 1 to 3,650 days, or empty to keep sources forever." };
  }
  try {
    await api.updateProject(slug, { name, tags, monthlyCreditCap: cap, retentionDays: retention, recallMinRelevance: minRelevance, recallHalfLifeDays: halfLife });
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not save." };
  }
  revalidatePath("/", "layout");
  return { saved: true };
}

export async function deleteProject(_prev: ProjectFormState, formData: FormData): Promise<ProjectFormState> {
  const slug = String(formData.get("slug"));
  if (String(formData.get("confirm") ?? "").trim() !== slug) return { error: `Type ${slug} to confirm.` };
  if ((await api.listProjects()).length <= 1) return { error: "This is the only project; create another before deleting it." };
  try {
    await api.deleteProject(slug);
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not delete the project." };
  }
  revalidatePath("/", "layout");
  return { deleted: slug };
}

export async function openProject(formData: FormData) {
  await api.selectProject(String(formData.get("slug")));
  revalidatePath("/", "layout");
  redirect("/");
}

export interface SecretState {
  error?: string;
  secret?: string;
  label?: string;
}

export async function createOrgKey(_prev: SecretState, formData: FormData): Promise<SecretState> {
  const name = String(formData.get("name") ?? "").trim();
  if (!name) return { error: "Give the key a name, such as ci or laptop." };
  try {
    const { secret } = await api.createOrgKey(name);
    revalidatePath("/settings");
    return { secret, label: name };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not create the key." };
  }
}

export async function revokeOrgKey(formData: FormData) {
  try {
    await api.revokeOrgKey(String(formData.get("id")));
  } catch {
    // The last key cannot be revoked; the list stays as it was.
  }
  revalidatePath("/settings");
}

export async function createOrg(_prev: SecretState, formData: FormData): Promise<SecretState> {
  const name = String(formData.get("name") ?? "").trim();
  if (!name) return { error: "Give the organization a name." };
  try {
    const { slug, secret } = await api.createOrg(name);
    revalidatePath("/settings");
    return { secret, label: `${name} (${slug})` };
  } catch (e) {
    const message = e instanceof Error ? e.message : "Could not create the organization.";
    return { error: message.includes("slug-taken") ? "An organization with that name exists." : message };
  }
}

export interface AudienceFormState {
  error?: string;
  saved?: string;
}

/** Create or replace an audience from the form: members one per line, or one trait condition. */
export async function saveAudience(_prev: AudienceFormState, formData: FormData): Promise<AudienceFormState> {
  const slug = String(formData.get("slug") ?? "").trim().toLowerCase();
  const kind = formData.get("kind") === "rule" ? "rule" : "fixed";
  if (!/^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/.test(slug) || slug === "project") {
    return { error: "Slug: 2-40 lowercase letters, digits and dashes; 'project' is reserved." };
  }
  const project = await api.currentProject();
  try {
    if (kind === "fixed") {
      const members = String(formData.get("members") ?? "").split(/[\s,]+/).map((m) => m.trim()).filter(Boolean);
      await api.putAudience(project.slug, slug, { kind, members });
    } else {
      const trait = String(formData.get("trait") ?? "").trim();
      const value = String(formData.get("value") ?? "").trim();
      if (!trait || !value) return { error: "A rule needs a trait and the value it must equal." };
      await api.putAudience(project.slug, slug, { kind, conditions: [{ trait, op: "is", value }] });
    }
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not save the audience." };
  }
  revalidatePath("/audiences");
  return { saved: slug };
}

export async function deleteAudience(formData: FormData) {
  const project = await api.currentProject();
  try {
    await api.deleteAudience(project.slug, String(formData.get("slug")));
  } catch {
    // An audience still named by episodes cannot be deleted; the list stays as it was.
  }
  revalidatePath("/audiences");
}

/** Rebuild a profile now (owners and admins): one person's, the project's, or all of them. */
export async function rebuildProfile(formData: FormData) {
  const project = await api.currentProject();
  const kind = String(formData.get("subjectKind") ?? "");
  const subject = String(formData.get("subject") ?? "");
  await api.rebuildProfiles(project.slug, kind === "person" || kind === "project" ? { subjectKind: kind, subject } : undefined);
  revalidatePath("/people");
  revalidatePath("/");
}

export interface AskState {
  query?: string;
  /** The answer, and the recall it was written from. */
  result?: Awaited<ReturnType<typeof api.ask>>;
  budget?: number;
  error?: string;
}

export async function runAsk(_prev: AskState, formData: FormData): Promise<AskState> {
  const query = String(formData.get("query") ?? "").trim();
  const budget = Number(formData.get("budget") ?? 2000);
  if (query.length < 3) return { query, error: "Type a question, the way an agent would ask it." };
  const project = await api.currentProject();
  try {
    return { query, budget, result: await api.ask(project.slug, query, budget, { related: true }) };
  } catch (e) {
    return { query, error: e instanceof Error ? e.message : "The question could not be asked." };
  }
}

export interface DeleteSessionState {
  done?: string;
  error?: string;
}

export async function deleteSession(_prev: DeleteSessionState, formData: FormData): Promise<DeleteSessionState> {
  const project = await api.currentProject();
  try {
    const r = await api.deleteSession(project.slug, String(formData.get("id")));
    revalidatePath("/sessions");
    revalidatePath("/memories");
    return { done: `Deleted ${r.messages} messages, ${r.sources} sources and ${r.memories} memories.` };
  } catch (e) {
    return { error: e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : "Could not delete the session." };
  }
}

export interface AddState {
  result?: Awaited<ReturnType<typeof api.addNow>>;
  /** What was sent, so the box keeps it: React resets a form after its action. */
  content?: string;
  error?: string;
}

/** Learn from pasted text within the request (POST /v1/ingest, instant). The page is not
 *  revalidated: the facts appear in place, and the person moves on to their memory when they choose. */
export async function addToMemory(_prev: AddState, formData: FormData): Promise<AddState> {
  const content = String(formData.get("content") ?? "").trim();
  if (content.length < 20) return { content, error: "Paste a few lines: a session, a standup or a PR thread." };
  if (content.length > INSTANT_MAX_CHARS) {
    return { content, error: `Paste at most ${INSTANT_MAX_CHARS.toLocaleString("en")} characters here; longer text goes through the API, where it is queued.` };
  }
  const project = await api.currentProject();
  if (!project.slug) return { content, error: "Create a project first." };
  try {
    return { content, result: await api.addNow(project.slug, content) };
  } catch (e) {
    return { content, error: e instanceof Error ? e.message.replace(/^\d+ [a-z-]+: /, "") : "Could not add it to memory." };
  }
}

// ---- memory rules: the project's memory instructions and its own categories (Settings → Memory rules)

export interface MemoryRulesState {
  error?: string;
  saved?: boolean;
}

const CATEGORY_SLUG_RE = /^[a-z0-9-]{2,24}$/;

/** A server error like "409 category-limit: ..." as a sentence for the form. */
function categoryError(e: unknown): string {
  const message = e instanceof Error ? e.message : "Could not save.";
  if (message.includes("category-limit")) return `A project has at most ${MAX_PROJECT_CATEGORIES} categories of its own; remove one first.`;
  if (message.includes("category-exists")) return "This project already has a category with that slug.";
  if (message.includes("category-builtin")) return "That slug is a built-in category.";
  return message;
}

export async function saveMemoryInstructions(_prev: MemoryRulesState, formData: FormData): Promise<MemoryRulesState> {
  const { slug } = await api.currentProject();
  const text = String(formData.get("instructions") ?? "").trim();
  if (text.length > MAX_MEMORY_INSTRUCTIONS) return { error: `Keep the instructions under ${MAX_MEMORY_INSTRUCTIONS.toLocaleString()} characters.` };
  try {
    await api.updateProject(slug, { memoryInstructions: text || null });
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not save." };
  }
  revalidatePath("/settings");
  return { saved: true };
}

export async function saveMemoryMode(_prev: MemoryRulesState, formData: FormData): Promise<MemoryRulesState> {
  const { slug } = await api.currentProject();
  const mode = String(formData.get("mode") ?? "");
  if (mode !== "coding" && mode !== "conversation") return { error: "Choose one of the two." };
  try {
    await api.updateProject(slug, { memoryMode: mode });
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not save." };
  }
  revalidatePath("/settings");
  return { saved: true };
}

export async function saveSharePolicy(_prev: MemoryRulesState, formData: FormData): Promise<MemoryRulesState> {
  const { slug } = await api.currentProject();
  const policy = String(formData.get("policy") ?? "");
  if (policy !== "ask" && policy !== "auto") return { error: "Choose one of the two." };
  try {
    await api.updateProject(slug, { sharePolicy: policy });
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Could not save." };
  }
  revalidatePath("/settings");
  return { saved: true };
}

export async function addCategory(_prev: MemoryRulesState, formData: FormData): Promise<MemoryRulesState> {
  const project = (await api.currentProject()).slug;
  const name = String(formData.get("name") ?? "").trim();
  const description = String(formData.get("description") ?? "").trim();
  const slug = (String(formData.get("slug") ?? "").trim() || name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 24);
  if (!name) return { error: "Give the category a name." };
  if (name.length > 60) return { error: "Keep the name under 60 characters." };
  if (description.length > 300) return { error: "Keep the description under 300 characters." };
  if (!CATEGORY_SLUG_RE.test(slug)) return { error: "The slug is 2 to 24 lowercase letters, digits and dashes." };
  if ((BUILTIN_CATEGORIES as string[]).includes(slug)) return { error: "That slug is a built-in category." };
  try {
    await api.createCategory(project, { slug, name, description });
  } catch (e) {
    return { error: categoryError(e) };
  }
  revalidatePath("/", "layout");
  return { saved: true };
}

export async function removeCategory(formData: FormData) {
  await api.deleteCategory((await api.currentProject()).slug, String(formData.get("slug")));
  revalidatePath("/", "layout");
}
