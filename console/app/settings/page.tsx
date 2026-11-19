import Link from "next/link";
import type { ReactNode } from "react";
import { Settings } from "lucide-react";
import { changeRole, createOrg, createOrgKey, removeMember, revokeInvite, revokeOrgKey, switchOrg } from "@/app/actions";
import {
  Badge, button, Card, CardBody, CardHeader, formatDate, Mono, PageHeader, Select, Table, TableEmpty, TBody, Td, Th, THead, timeAgo, Tr,
} from "@/components/ui";
import { currentProject, listAudit, listInvites, listMembers, listOrgKeys, listOrgs, listProjects, orgInfo } from "@/lib/api";
import { CategoriesEditor, InstructionsForm, ModeForm, SharePolicyForm } from "./MemoryRules";
import { InviteForm, PasswordForm, ResetLinkForm } from "./PeopleForms";
import { SecretForm } from "./SecretForm";

function Section({ id, title, description, children }: { id: string; title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-6 border-t border-border pt-8 first:border-t-0 first:pt-0">
      <div className="mb-4">
        <h2 id={`${id}-title`} className="text-[15px] font-semibold tracking-[-0.01em] text-text">{title}</h2>
        {description && <p className="mt-1 max-w-2xl text-[13px] text-text-2">{description}</p>}
      </div>
      {children}
    </section>
  );
}

export default async function SettingsPage() {
  const [info, projects, project] = await Promise.all([orgInfo(), listProjects(), currentProject()]);
  const manager = info.operator || info.role === "owner" || info.role === "admin";
  const [keys, orgs, members, invites, audit] = await Promise.all([
    info.operator || !manager ? [] : listOrgKeys(),
    info.operator ? listOrgs() : [],
    info.operator ? [] : listMembers(),
    info.operator || !manager ? [] : listInvites(),
    manager ? listAudit() : [],
  ]);
  const liveKeys = keys.filter((k) => !k.revokedAt).length;
  const owners = members.filter((m) => m.role === "owner").length;
  const openInvites = invites.filter((i) => !i.acceptedAt);
  // Whose password this person may reset: not their own (Settings → password), an owner's only as an owner.
  const resettable = members.filter((m) => m.id !== info.user?.id && (m.role !== "owner" || info.role === "owner"))
    .map((m) => ({ id: m.id, email: m.email }));

  return (
    <>
      <PageHeader
        icon={Settings}
        title="Settings"
        description={info.operator ? "You are signed in with the operator key: every organization is visible." : `Organization: ${info.org?.name ?? ""}`}
      />

      <div className="space-y-8">
        {info.user && (
          <Section id="account" title="Your account">
            <Card>
              <CardBody className="space-y-5">
                <div className="flex items-center gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-full bg-surface-3 text-[13px] font-semibold text-text-2" aria-hidden>
                    {(info.user.name || info.user.email).slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-[14px] font-medium text-text">{info.user.name || info.user.email}</p>
                    <p className="truncate text-[12.5px] text-text-3">{info.user.email} · {info.role} of {info.org?.name}</p>
                  </div>
                </div>
                {info.orgs.length > 1 && (
                  <form action={switchOrg} className="flex flex-wrap items-center gap-2 text-[13px]">
                    <label htmlFor="org" className="text-text-2">Switch organization</label>
                    <Select id="org" name="org" defaultValue={info.org?.slug} wrapperClassName="w-[240px]">
                      {info.orgs.map((o) => <option key={o.slug} value={o.slug}>{o.name} ({o.role})</option>)}
                    </Select>
                    <button className={button("secondary")}>Switch</button>
                  </form>
                )}
                <div>
                  <p className="mb-2 text-[12.5px] font-medium text-text-2">Change password</p>
                  <PasswordForm />
                </div>
              </CardBody>
            </Card>
          </Section>
        )}

        {project.slug && (
          <Section
            id="memory-rules"
            title={`Memory rules · ${project.name}`}
            description={<>What memory keeps for this project, on top of the built-in rules. They apply to conversations synthesized from now on; switch project in the header to edit another.{!manager && " Only owners and admins can change them."}</>}
          >
            <Card>
              <CardBody className="space-y-6">
                {/* key: another project resets the field */}
                {project.teamSpace ? (
                  /* A team space makes no memory from conversation: only how memories reach it is set here. */
                  <SharePolicyForm key={`share-${project.slug}-${project.sharePolicy ?? "ask"}`} value={project.sharePolicy ?? "ask"} canEdit={manager} />
                ) : (
                  <>
                    <ModeForm key={`mode-${project.slug}-${project.memoryMode ?? "coding"}`} value={project.memoryMode ?? "coding"} canEdit={manager} />
                    <InstructionsForm key={project.slug} value={project.memoryInstructions ?? ""} canEdit={manager} />
                  </>
                )}
                <CategoriesEditor categories={project.categories ?? []} canEdit={manager} />
              </CardBody>
            </Card>
          </Section>
        )}

        {!info.operator && (
          <Section id="members" title="Members" description="People who sign in to this organization, and their role.">
            {manager && (
              <Card className="mb-4">
                <CardBody><InviteForm /></CardBody>
              </Card>
            )}
            {manager && resettable.length > 0 && (
              <Card className="mb-4">
                <CardHeader title="Reset a password" description="For a member who forgot theirs. Someone who also belongs to another organization gets a link from the server's operator instead." />
                <CardBody><ResetLinkForm people={resettable} /></CardBody>
              </Card>
            )}
            <Table minWidth={620}>
              <THead>
                <Th>Person</Th>
                <Th className="w-[200px]">Role</Th>
                <Th className="w-[150px]">Last sign-in</Th>
                <Th align="right" className="w-[110px]"><span className="sr-only">Actions</span></Th>
              </THead>
              <TBody>
                {members.length === 0 && <TableEmpty colSpan={4}>No people yet: this organization is used with keys only. Invite someone above.</TableEmpty>}
                {members.map((m) => {
                  const self = m.id === info.user?.id;
                  const lastOwner = m.role === "owner" && owners <= 1;
                  return (
                    <Tr key={m.id}>
                      <Td className="py-3">
                        <div className="font-medium text-text">{m.name || m.email}{self && <span className="font-normal text-text-3"> (you)</span>}</div>
                        <div className="text-[12px] text-text-3">{m.email}</div>
                      </Td>
                      <Td>
                        {manager && !lastOwner && (m.role !== "owner" || info.role === "owner") ? (
                          <form action={changeRole} className="flex items-center gap-1.5">
                            <input type="hidden" name="id" value={m.id} />
                            <Select name="role" defaultValue={m.role} aria-label={`Role of ${m.email}`} wrapperClassName="w-[110px]" className="h-7 py-0! text-[12.5px]">
                              {info.role === "owner" && <option value="owner">owner</option>}
                              <option value="admin">admin</option>
                              <option value="member">member</option>
                            </Select>
                            <button className={button("ghost", "sm")}>Save</button>
                          </form>
                        ) : <Badge>{m.role}</Badge>}
                      </Td>
                      <Td muted nowrap>{timeAgo(m.lastLoginAt)}</Td>
                      <Td align="right">
                        {!lastOwner && (self || (manager && (m.role !== "owner" || info.role === "owner"))) && (
                          <form action={removeMember}>
                            <input type="hidden" name="id" value={m.id} />
                            <button className={button("danger", "sm")}>{self ? "Leave" : "Remove"}</button>
                          </form>
                        )}
                      </Td>
                    </Tr>
                  );
                })}
              </TBody>
            </Table>
            {openInvites.length > 0 && (
              <>
                <h3 className="mt-6 mb-2 text-[13px] font-medium text-text">Open invites</h3>
                <Table minWidth={560}>
                  <THead>
                    <Th>Invited</Th>
                    <Th className="w-[100px]">Role</Th>
                    <Th>Created</Th>
                    <Th align="right" className="w-[100px]"><span className="sr-only">Actions</span></Th>
                  </THead>
                  <TBody>
                    {openInvites.map((i) => (
                      <Tr key={i.id}>
                        <Td>{i.email ?? <span className="text-text-2">Anyone with the link</span>}</Td>
                        <Td><Badge>{i.role}</Badge></Td>
                        <Td muted className="text-[12.5px]">by {i.createdBy}, expires {formatDate(i.expiresAt)}</Td>
                        <Td align="right">
                          <form action={revokeInvite}>
                            <input type="hidden" name="id" value={i.id} />
                            <button className={button("danger", "sm")}>Revoke</button>
                          </form>
                        </Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </>
            )}
          </Section>
        )}

        {info.operator ? (
          <Section id="organizations" title={`${orgs.length} organization${orgs.length === 1 ? "" : "s"}`}>
            <Card className="mb-4">
              <CardBody>
                <SecretForm action={createOrg} placeholder="New organization name, e.g. Acme Inc" button="Create organization"
                  note="This is the organization's management key. Send it to the customer: they sign in to the console with it and see only their own projects. It is not shown again." />
              </CardBody>
            </Card>
            <Table minWidth={520}>
              <THead>
                <Th>Organization</Th>
                <Th align="right">Projects</Th>
                <Th align="right">Created</Th>
              </THead>
              <TBody>
                {orgs.length === 0 && <TableEmpty colSpan={3}>No organizations yet.</TableEmpty>}
                {orgs.map((o) => (
                  <Tr key={o.slug}>
                    <Td className="py-3"><div className="font-medium">{o.name}</div><Mono className="text-text-3">{o.slug}</Mono></Td>
                    <Td align="right" muted>{o.projectCount}</Td>
                    <Td align="right" muted>{formatDate(o.createdAt)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </Section>
        ) : manager && (
          <Section id="keys" title="Organization keys" description="For scripts and CI that administer this organization through the API. People sign in with their own accounts instead.">
            <Card className="mb-4">
              <CardBody>
                <SecretForm action={createOrgKey} placeholder="Key name, e.g. priya or ci" button="Create key"
                  note="Store it now; it is not shown again. It signs in to this console with full access to this organization." />
              </CardBody>
            </Card>
            <Table minWidth={560}>
              <THead>
                <Th>Name</Th>
                <Th>Key</Th>
                <Th>Last used</Th>
                <Th align="right"><span className="sr-only">Actions</span></Th>
              </THead>
              <TBody>
                {keys.length === 0 && <TableEmpty colSpan={4}>No organization keys.</TableEmpty>}
                {keys.map((k) => (
                  <Tr key={k.id} className={k.revokedAt ? "text-text-3" : ""}>
                    <Td className={k.revokedAt ? "" : "font-medium"}>{k.name}</Td>
                    <Td><Mono>{k.masked}</Mono></Td>
                    <Td muted nowrap>{timeAgo(k.lastUsedAt)}</Td>
                    <Td align="right">
                      {k.revokedAt ? <Badge>revoked {formatDate(k.revokedAt)}</Badge> : (
                        <form action={revokeOrgKey}>
                          <input type="hidden" name="id" value={k.id} />
                          <button className={button("danger", "sm")} disabled={liveKeys <= 1} title={liveKeys <= 1 ? "Create another key before revoking the last one" : undefined}>Revoke</button>
                        </form>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </Section>
        )}

        {manager && (
          <Section
            id="audit"
            title="Audit log"
            description={<>Every change to projects, keys, groups, identities and memories, with the key that made it. The last 50 are shown; <code className="font-mono text-[12px]">GET /v1/audit</code> returns more.</>}
          >
            <Table minWidth={680}>
              <THead>
                <Th className="w-[110px]">When</Th>
                <Th>Action</Th>
                <Th>Target</Th>
                <Th>Project</Th>
                <Th align="right">Actor</Th>
              </THead>
              <TBody>
                {audit.length === 0 && <TableEmpty colSpan={5}>No changes recorded yet.</TableEmpty>}
                {audit.map((e, i) => (
                  <Tr key={i}>
                    <Td muted nowrap><span title={e.at}>{timeAgo(e.at)}</span></Td>
                    <Td><Mono className="text-text">{e.action}</Mono></Td>
                    <Td className="max-w-[260px]"><span className="block truncate font-mono text-[12px] text-text-2" title={e.target}>{e.target}</span></Td>
                    <Td muted className="text-[12.5px]">{e.project ?? ""}</Td>
                    <Td align="right" muted className="text-[12.5px]">{e.actor}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </Section>
        )}

        <Section id="projects" title="Projects">
          <p className="text-[13px] text-text-2">
            {projects.length} project{projects.length === 1 ? "" : "s"}. Create, rename, tag, cap and delete them on the{" "}
            <Link href="/projects" className="text-text underline decoration-border-strong underline-offset-2 hover:decoration-text">Projects</Link> page.
          </p>
        </Section>
      </div>
    </>
  );
}
