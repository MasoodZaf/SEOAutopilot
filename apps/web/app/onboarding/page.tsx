import {redirect} from "next/navigation";
import type {Metadata} from "next";

import {Eyebrow, Note, button, input} from "@/app/components/ui";
import {currentTenants} from "@/lib/server-api";

import {createWorkspaceAction} from "./actions";
import {messageFor} from "./messages";

type PageProps = {searchParams: Promise<{error?: string}>};

export const metadata: Metadata = {
  title: "Create a workspace",
  robots: {index: false, follow: false},
};

/**
 * The first screen of a new account.
 *
 * Somebody already in a workspace has no business here, so they are sent on --
 * this is not a workspace switcher, and offering a second one to a person who
 * wanted the first is how people end up with an empty duplicate.
 */
export default async function OnboardingPage({searchParams}: PageProps) {
  const {error} = await searchParams;
  const tenants = await currentTenants();
  if (tenants.length > 0) redirect("/pilot");

  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-8 px-6 py-20">
      <header className="flex flex-col gap-3">
        <Eyebrow>Step 1 of 3</Eyebrow>
        <h1 className="font-display text-[30px] leading-[1.15] font-semibold tracking-tight text-balance text-ink">
          Name your workspace
        </h1>
        <p className="text-pretty text-[13px] leading-6 text-ink-soft">
          A workspace holds your sites, your connectors and your data, and nobody
          else&rsquo;s. You will be its owner, and the only people who can ever see inside
          it are the ones you invite.
        </p>
      </header>

      {error ? (
        <Note tone="stop" role="alert" label="Not created">
          {messageFor(error)}
        </Note>
      ) : null}

      <form action={createWorkspaceAction} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink">Workspace name</span>
          <input
            name="name"
            required
            minLength={2}
            maxLength={200}
            autoFocus
            placeholder="Acme Ltd"
            className={input}
          />
          <span className="text-[12px] leading-5 text-ink-faint">
            Usually your company or your project. You can invite colleagues once it exists.
          </span>
        </label>
        <button type="submit" className={`${button.primary} self-start`}>
          Create workspace
        </button>
      </form>

      <section className="border-t border-rule pt-5">
        <Eyebrow>What happens next</Eyebrow>
        <ol className="mt-3 flex flex-col gap-2.5">
          {[
            "You add your own Google and GitHub credentials, so nothing runs through ours.",
            "You add a site and prove you control the domain with a DNS record.",
            "The crawler reads that site, and findings become proposals you approve.",
          ].map((step, index) => (
            <li key={step} className="flex gap-3 text-[13px] leading-6 text-ink-soft">
              <span className="mt-0.5 font-mono text-[11px] text-ink-faint tabular">
                {String(index + 2).padStart(2, "0")}
              </span>
              <span className="text-pretty">{step}</span>
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
