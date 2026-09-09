import {redirect} from "next/navigation";
import type {Metadata} from "next";

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
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-6 py-20">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-balance text-slate-900">
          Create your workspace
        </h1>
        <p className="text-pretty text-sm text-slate-600">
          A workspace holds your sites, your connectors and your data, and nobody else&rsquo;s.
          You will be its owner, and the only people who can ever see inside it are the ones you
          invite.
        </p>
      </header>

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {messageFor(error)}
        </p>
      ) : null}

      <form action={createWorkspaceAction} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-800">
          Workspace name
          <input
            name="name"
            required
            minLength={2}
            maxLength={200}
            autoFocus
            placeholder="Acme Ltd"
            className="rounded-md border border-slate-300 px-3 py-2 font-normal text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
          />
          <span className="text-xs font-normal text-slate-500">
            Usually your company or your project. You can invite colleagues once it exists.
          </span>
        </label>
        <button
          type="submit"
          className="self-start rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Create workspace
        </button>
      </form>

      <section className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-900">What happens next</h2>
        <ol className="mt-2 flex list-decimal flex-col gap-1 pl-4 text-sm text-slate-600">
          <li>You add your own Google and GitHub credentials, so nothing runs through ours.</li>
          <li>You add a site and prove you control the domain with a DNS record.</li>
          <li>The crawler reads that site, and findings become proposals you approve.</li>
        </ol>
      </section>
    </main>
  );
}
