// A profile's lines, each linked to the memories it restates (profiles are derived from memory,
// never a source of their own), and the form that rebuilds one.
import Link from "next/link";
import { rebuildProfile } from "@/app/actions";
import { button, timeAgo } from "./ui";
import type { Profile } from "@/lib/types";

const memoryHref = (id: string) => `/memories?open=${encodeURIComponent(id)}`;

export function ProfileLines({ profile, statements = {} }: { profile: Profile; statements?: Record<string, string> }) {
  if (!profile.lines.length && !profile.description) {
    return (
      <p className="text-[13px] text-text-3">
        {profile.builtAt ? "Nothing in memory to say yet." : "Not built yet: memory builds it within a minute or two of being asked."}
      </p>
    );
  }
  return (
    <ul className="space-y-2.5">
      {profile.description && (
        <li className="text-[13.5px] leading-snug text-text-2">
          {profile.description} <span className="text-[12px] text-text-3">· project description</span>
        </li>
      )}
      {profile.lines.map((line) => (
        <li key={line.text} className="text-[13.5px] leading-snug text-text">
          {line.text}
          <span className="ml-1.5 inline-flex flex-wrap gap-x-1.5 text-[12px] text-text-3">
            {line.memoryIds.map((id, i) => (
              <Link key={id} href={memoryHref(id)} className="underline decoration-text-3/40 underline-offset-2 hover:text-text"
                    title={statements[id] ?? "Open the memory this line restates"}>
                {line.memoryIds.length === 1 ? "from memory" : `memory ${i + 1}`}
              </Link>
            ))}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ProfileMeta({ profile }: { profile: Profile }) {
  return (
    <span className="text-[12px] text-text-3">
      {profile.builtAt ? `built ${timeAgo(profile.builtAt)}` : "not built yet"}
      {profile.dirty && profile.builtAt ? " · memory changed, rebuild due" : ""}
      {profile.text ? ` · ${profile.tokens} tokens` : ""}
    </span>
  );
}

export function RebuildButton({ subjectKind, subject = "" }: { subjectKind?: "person" | "project"; subject?: string }) {
  return (
    <form action={rebuildProfile}>
      {subjectKind && <input type="hidden" name="subjectKind" value={subjectKind} />}
      <input type="hidden" name="subject" value={subject} />
      <button className={button("secondary", "sm")}>{subjectKind ? "Rebuild" : "Rebuild all"}</button>
    </form>
  );
}
