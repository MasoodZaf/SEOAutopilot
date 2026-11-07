"use server";

import {revalidatePath} from "next/cache";
import {redirect, unstable_rethrow} from "next/navigation";

import {ApiError, apiJson} from "@/lib/server-api";

const PAGE = "/settings/billing";

function redirectFresh(path: string): never {
  revalidatePath(PAGE, "page");
  redirect(path);
}

async function goToStripe(path: string): Promise<never> {
  let url: string;
  try {
    url = (await apiJson<{data: {url: string}}>(path, {method: "POST", body: "{}"})).data.url;
  } catch (error) {
    unstable_rethrow(error);
    const code = error instanceof ApiError ? error.code : "unexpected_error";
    redirectFresh(`${PAGE}?error=${encodeURIComponent(code)}`);
  }
  redirectFresh(url);
}

export async function startCheckoutAction(): Promise<never> {
  return goToStripe("/v1/billing/checkout");
}

export async function openPortalAction(): Promise<never> {
  return goToStripe("/v1/billing/portal");
}
