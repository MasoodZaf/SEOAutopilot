import type {Metadata} from "next";
import {redirect} from "next/navigation";

import {button} from "@/app/components/ui";
import {apiJson, isMissingTenant} from "@/lib/server-api";

import {openPortalAction, startCheckoutAction} from "./actions";

export const metadata: Metadata = {
  title: "Billing",
  robots: {index: false, follow: false},
};

type Billing = {
  configured: boolean;
  enforced: boolean;
  status: string;
  has_access: boolean;
  trial_ends_at: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  has_billing_account: boolean;
};

const REASONS: Record<string, string> = {
  billing_provider_error: "Stripe did not answer. Nothing was charged. Try again in a minute.",
  billing_not_enabled: "Billing is not switched on yet.",
  billing_not_required: "This workspace does not need a subscription.",
  no_billing_account: "There is no billing account for this workspace yet.",
  subscription_already_active: "This workspace already has a subscription.",
  forbidden: "Only a workspace owner can change billing.",
  unexpected_error: "That did not work. Try again.",
};

function day(value: string | null): string {
  return value
    ? new Date(value).toLocaleDateString("en-GB", {day: "numeric", month: "long", year: "numeric"})
    : "";
}

function summary(billing: Billing): string {
  switch (billing.status) {
    case "comped":
      return "This workspace is free. There is nothing to pay.";
    case "trialing":
      return billing.has_access
        ? `Your trial runs until ${day(billing.trial_ends_at)}.`
        : "Your trial has ended. Subscribe to run crawls, publish changes and use the assistant again. Your data is still here.";
    case "active":
      return billing.cancel_at_period_end
        ? `Your subscription ends on ${day(billing.current_period_end)}.`
        : `Your subscription renews on ${day(billing.current_period_end)}.`;
    case "past_due":
      return "Your last payment failed. Stripe will try again. Check your card to avoid losing access.";
    default:
      return "There is no active subscription. Subscribe to run crawls, publish changes and use the assistant. Your data is still here.";
  }
}

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{error?: string; checkout?: string}>;
}) {
  const query = await searchParams;
  let billing: Billing;
  try {
    billing = (await apiJson<{data: Billing}>("/v1/billing")).data;
  } catch (error) {
    if (isMissingTenant(error)) redirect("/onboarding");
    throw error;
  }
  const canSubscribe =
    billing.configured && !["comped", "active", "past_due"].includes(billing.status);

  return (
    <main className="flex max-w-3xl flex-col gap-6 px-6 py-10">
      <h1 className="font-display text-[32px] leading-tight font-light tracking-tight text-balance text-ink">
        Billing
      </h1>

      {query.checkout === "done" ? (
        <p role="status" className="rounded-xl border border-good-rule bg-good-soft px-3 py-2 text-sm text-good">
          Thanks. Your subscription will show here as soon as Stripe confirms it.
        </p>
      ) : null}

      <p className="text-pretty text-sm text-ink">
        {billing.configured ? summary(billing) : "SEO Autopilot is free while it is in testing."}
      </p>

      {query.error ? (
        <p role="alert" className="rounded-xl border border-stop-rule bg-stop-soft px-3 py-2 text-sm text-stop">
          {REASONS[query.error] ?? REASONS.unexpected_error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-4">
        {canSubscribe ? (
          <form action={startCheckoutAction}>
            <button type="submit" className={button.primary}>
              Subscribe
            </button>
          </form>
        ) : null}
        {billing.configured && billing.has_billing_account ? (
          <form action={openPortalAction}>
            <button type="submit" className={button.secondary}>
              Manage card, invoices and cancellation
            </button>
          </form>
        ) : null}
      </div>
    </main>
  );
}
