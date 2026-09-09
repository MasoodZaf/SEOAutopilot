/**
 * The pending DNS challenge, held between issuing it and checking it.
 *
 * Deliberately not in `actions.ts`: a `"use server"` module may only export
 * async functions, so a constant and a type living there fail the build with
 * an error that names the export rather than the rule.
 */
export const CHALLENGE_COOKIE = "seo-autopilot-site-challenge";

export type StoredChallenge = {
  siteId: string;
  token: string;
  recordName: string;
  recordValue: string;
  expiresAt: string;
};
