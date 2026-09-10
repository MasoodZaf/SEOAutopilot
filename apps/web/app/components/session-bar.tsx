import {currentSession} from "@/lib/server-api";

import {button} from "./ui";

/**
 * Who is signed in, and the way out.
 *
 * It used to take a `tone` prop, because /pilot was dark and /settings was
 * light and the same bar had to be drawn twice. There is one palette now, so
 * there is one bar; the prop is gone rather than defaulted, so nothing can pass
 * it and quietly get the old two-palette behaviour back.
 *
 * Renders nothing when no identity provider is configured, so a local
 * development stack that still uses the pilot token is unchanged.
 */
export async function SessionBar() {
  const session = await currentSession();
  if (!session) return null;

  return (
    <div className="flex items-center gap-3 text-[13px]">
      <span className="hidden truncate text-ink-faint sm:inline">
        {session.email || session.name}
      </span>
      <form action="/auth/logout" method="post">
        <button type="submit" className={button.secondary}data-tip="End this session on this browser. Your workspace, keys and sites are untouched." data-tip-side="bottom">
          Sign out
        </button>
      </form>
    </div>
  );
}
