import {currentSession} from "@/lib/server-api";
import {cn} from "@/lib/cn";

/**
 * Who is signed in, and the way out.
 *
 * Renders nothing when no identity provider is configured, so a local
 * development stack that still uses the pilot token is unchanged.
 */
export async function SessionBar({tone = "dark"}: {tone?: "dark" | "light"}) {
  const session = await currentSession();
  if (!session) return null;

  return (
    <div
      className={cn(
        "flex items-center justify-end gap-3 border-b px-6 py-2 text-sm",
        tone === "dark"
          ? "border-zinc-800 bg-zinc-950 text-zinc-400"
          : "border-slate-200 bg-white text-slate-600",
      )}
    >
      <span className="truncate">
        Signed in as{" "}
        <span className={cn("font-medium", tone === "dark" ? "text-zinc-100" : "text-slate-900")}>
          {session.email || session.name}
        </span>
      </span>
      <form action="/auth/logout" method="post">
        <button
          type="submit"
          className={cn(
            "rounded-md border px-2.5 py-1 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
            tone === "dark"
              ? "border-zinc-700 bg-zinc-900 text-zinc-100 hover:bg-zinc-800 focus-visible:outline-zinc-100"
              : "border-slate-300 bg-white text-slate-800 hover:bg-slate-100 focus-visible:outline-slate-900",
          )}
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
