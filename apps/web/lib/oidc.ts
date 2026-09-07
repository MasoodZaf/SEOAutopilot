import "server-only";

import {createHash, randomBytes} from "node:crypto";

/**
 * The browser-facing half of the OIDC flow.
 *
 * The API verifies tokens against the provider's key set; this side runs the
 * authorization code exchange and holds the result. PKCE is used even though
 * this is a confidential client with a secret, because the verifier also binds
 * the redirect back to the browser that started it -- a code intercepted from
 * the redirect is useless without the cookie holding the verifier.
 */

export type ProviderMetadata = {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
};

export type TokenResponse = {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in: number;
};

let cached: ProviderMetadata | null = null;

export function issuer(): string {
  const value = process.env.OIDC_ISSUER_URL;
  if (!value) throw new Error("oidc-not-configured");
  return value.replace(/\/$/, "");
}

export function clientId(): string {
  const value = process.env.OIDC_CLIENT_ID;
  if (!value) throw new Error("oidc-not-configured");
  return value;
}

function clientSecret(): string {
  const value = process.env.OIDC_CLIENT_SECRET;
  if (!value) throw new Error("oidc-not-configured");
  return value;
}

export function redirectUri(): string {
  const base = process.env.APP_BASE_URL ?? "http://localhost:3000";
  return `${base.replace(/\/$/, "")}/api/auth/callback`;
}

export function isConfigured(): boolean {
  return Boolean(
    process.env.OIDC_ISSUER_URL && process.env.OIDC_CLIENT_ID && process.env.OIDC_CLIENT_SECRET,
  );
}

export async function metadata(): Promise<ProviderMetadata> {
  if (cached) return cached;
  const response = await fetch(`${issuer()}/.well-known/openid-configuration`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error("oidc-discovery-failed");
  const document = (await response.json()) as ProviderMetadata & {issuer?: string};
  if (!document.authorization_endpoint || !document.token_endpoint) {
    throw new Error("oidc-discovery-invalid");
  }
  if ((document.issuer ?? "").replace(/\/$/, "") !== issuer()) {
    throw new Error("oidc-discovery-issuer-mismatch");
  }
  cached = document;
  return document;
}

export function pkce(): {verifier: string; challenge: string} {
  const verifier = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  return {verifier, challenge};
}

export async function authorizationUrl(state: string, challenge: string, nonce: string) {
  const provider = await metadata();
  const url = new URL(provider.authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", clientId());
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("state", state);
  url.searchParams.set("nonce", nonce);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

export async function exchangeCode(code: string, verifier: string): Promise<TokenResponse> {
  const provider = await metadata();
  const response = await fetch(provider.token_endpoint, {
    method: "POST",
    cache: "no-store",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri(),
      client_id: clientId(),
      client_secret: clientSecret(),
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new Error("oidc-code-exchange-failed");
  return (await response.json()) as TokenResponse;
}

export async function refresh(refreshToken: string): Promise<TokenResponse> {
  const provider = await metadata();
  const response = await fetch(provider.token_endpoint, {
    method: "POST",
    cache: "no-store",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: clientId(),
      client_secret: clientSecret(),
    }),
  });
  if (!response.ok) throw new Error("oidc-refresh-failed");
  return (await response.json()) as TokenResponse;
}

/**
 * The claims of an ID token, read without verifying its signature.
 *
 * Safe only here: this token came back over TLS from the provider's own token
 * endpoint in response to a code this server exchanged with its client secret,
 * which is what establishes it. The API, which receives tokens from clients
 * rather than from the provider, verifies every one against the key set.
 */
export function readClaims(idToken: string | undefined): {email: string; name: string; nonce?: string} {
  if (!idToken) return {email: "", name: ""};
  const [, payload] = idToken.split(".");
  if (!payload) return {email: "", name: ""};
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as {
      email?: string;
      name?: string;
      nonce?: string;
    };
    return {email: claims.email ?? "", name: claims.name ?? "", nonce: claims.nonce};
  } catch {
    return {email: "", name: ""};
  }
}
