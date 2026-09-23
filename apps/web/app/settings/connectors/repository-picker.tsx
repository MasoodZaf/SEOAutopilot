"use client";

import {useRouter} from "next/navigation";
import {useEffect, useMemo, useRef, useState} from "react";

import {Badge, button, input, inputMono} from "@/app/components/ui";

import {chooseRepositoryAction, connectGitHubAction} from "./actions";

export type RepositoryChoice = {
  id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  account: string;
};

/**
 * The list of repositories after signing in with GitHub, as Claude or ChatGPT
 * shows it.
 *
 * A native modal <dialog>: it traps focus, closes on Escape and restores focus
 * without a library. Closing it only leaves the page -- nothing was connected
 * yet, so there is nothing to undo.
 */
export function RepositoryPicker({
  siteId,
  siteName,
  repositories,
  previousTemplate,
}: {
  siteId: string;
  siteName: string;
  repositories: RepositoryChoice[];
  previousTemplate?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<number | null>(
    repositories.length === 1 ? repositories[0].id : null,
  );

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle
      ? repositories.filter((item) => item.full_name.toLowerCase().includes(needle))
      : repositories;
  }, [filter, repositories]);
  const chosen = repositories.find((item) => item.id === picked);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="picker-heading"
      onClose={() => router.replace("/settings/connectors")}
      className="m-auto w-[min(36rem,calc(100vw-2rem))] rounded-[4px] border border-rule bg-surface p-0 text-ink backdrop:bg-ink/40"
    >
      <div className="border-b border-rule px-5 py-4">
        <h2 id="picker-heading" className="text-balance text-base font-semibold">
          Choose a repository for {siteName}
        </h2>
        <p className="mt-1 text-pretty text-[13px] text-ink-soft">
          These are the repositories you can push to that SEO Autopilot can reach. Approved
          changes arrive in the one you pick as pull requests.
        </p>
      </div>

      {repositories.length === 0 ? (
        <div className="px-5 py-6">
          <p className="text-pretty text-[13px] text-ink-soft">
            GitHub did not return a repository you can push to. Give SEO Autopilot access to the
            one this site is built from.
          </p>
          <form action={connectGitHubAction} className="mt-4">
            <input type="hidden" name="site_id" value={siteId} />
            <input type="hidden" name="install" value="1" />
            <button type="submit" className={button.primary}>
              Add a repository on GitHub
            </button>
          </form>
        </div>
      ) : (
        <form action={chooseRepositoryAction}>
          <input type="hidden" name="site_id" value={siteId} />
          <div className="px-5 pt-4">
            {repositories.length > 6 ? (
              <input
                type="search"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Filter repositories"
                aria-label="Filter repositories"
                className={input}
              />
            ) : null}
            <fieldset className="mt-3 max-h-72 overflow-y-auto rounded-[4px] border border-rule">
              <legend className="sr-only">Repository</legend>
              {shown.map((item) => (
                <label
                  key={item.id}
                  className="flex cursor-pointer items-center gap-3 border-b border-rule px-3 py-2.5 last:border-b-0 hover:bg-sunk has-[:checked]:bg-accent-soft"
                >
                  <input
                    type="radio"
                    name="repository_id"
                    value={item.id}
                    required
                    checked={picked === item.id}
                    onChange={() => setPicked(item.id)}
                  />
                  <span className="min-w-0 grow truncate font-mono text-[13px]">
                    {item.full_name}
                  </span>
                  <Badge>{item.private ? "Private" : "Public"}</Badge>
                </label>
              ))}
              {shown.length === 0 ? (
                <p className="px-3 py-3 text-[13px] text-ink-faint">No repository matches.</p>
              ) : null}
            </fieldset>
          </div>

          <details className="px-5 pt-3">
            <summary className={`${button.quiet} cursor-pointer list-none`}>
              Branch and file layout
            </summary>
            <div className="mt-3 grid gap-3">
              <label className="text-[13px] text-ink">
                Base branch
                <input
                  name="base_branch"
                  autoComplete="off"
                  placeholder={chosen?.default_branch ?? "the repository's default branch"}
                  className={`${inputMono} mt-1`}
                />
              </label>
              <label className="text-[13px] text-ink">
                Path template
                <input
                  name="path_template"
                  defaultValue={previousTemplate ?? "{path}.html"}
                  autoComplete="off"
                  className={`${inputMono} mt-1`}
                />
                <span className="mt-1 block text-xs font-normal text-ink-faint">
                  How a page&rsquo;s URL becomes a file in the repository. For{" "}
                  <span className="font-mono">/about</span> stored at{" "}
                  <span className="font-mono">site/about.html</span>, use{" "}
                  <span className="font-mono">{"site/{path}.html"}</span>.
                </span>
              </label>
            </div>
          </details>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-rule px-5 py-4">
            <button
              type="submit"
              formAction={connectGitHubAction}
              formNoValidate
              name="install"
              value="1"
              className={button.quiet}
            >
              Missing a repository? Add it on GitHub
            </button>
            <div className="flex gap-2">
              <button
                type="button"
                className={button.secondary}
                onClick={() => dialog.current?.close()}
              >
                Cancel
              </button>
              <button type="submit" className={button.primary} disabled={picked === null}>
                Connect repository
              </button>
            </div>
          </div>
        </form>
      )}
    </dialog>
  );
}
