import "server-only";

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
const pilotToken = process.env.LOCAL_PILOT_AUTH_TOKEN;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
  }
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!pilotToken || pilotToken.length < 32) {
    throw new ApiError(503, "pilot-session-not-configured");
  }
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${pilotToken}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let code = `api-error-${response.status}`;
    try {
      const body = (await response.json()) as {detail?: string};
      if (typeof body.detail === "string") code = body.detail;
    } catch {
      // Retain the bounded status-derived code.
    }
    throw new ApiError(response.status, code);
  }
  return (await response.json()) as T;
}
