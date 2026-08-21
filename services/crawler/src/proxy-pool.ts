export type ProxyProtocol = "http" | "https" | "socks5";

export interface ProxyEntry {
  url: string;
  protocol: ProxyProtocol;
  region: string;
  healthy: boolean;
  failureCount: number;
  lastUsedAt: number;
}

export class ProxyPoolManager {
  private proxies: ProxyEntry[] = [];
  private currentIndex = 0;
  private maxConsecutiveFailures = 3;

  constructor(initialProxies: Array<{url: string; protocol?: ProxyProtocol; region?: string}> = []) {
    for (const p of initialProxies) {
      this.addProxy(p.url, p.protocol ?? "http", p.region ?? "global");
    }
  }

  public addProxy(url: string, protocol: ProxyProtocol = "http", region = "global"): void {
    this.proxies.push({
      url,
      protocol,
      region,
      healthy: true,
      failureCount: 0,
      lastUsedAt: 0,
    });
  }

  public getNextProxy(targetRegion?: string): ProxyEntry | null {
    const candidates = this.proxies.filter(
      (p) => p.healthy && (!targetRegion || p.region === targetRegion || p.region === "global"),
    );

    if (candidates.length === 0) {
      return null;
    }

    this.currentIndex = (this.currentIndex + 1) % candidates.length;
    const selected = candidates[this.currentIndex] ?? candidates[0];
    if (selected) {
      selected.lastUsedAt = Date.now();
      return selected;
    }
    return null;
  }

  public reportSuccess(url: string): void {
    const entry = this.proxies.find((p) => p.url === url);
    if (entry) {
      entry.failureCount = 0;
      entry.healthy = true;
    }
  }

  public reportFailure(url: string): void {
    const entry = this.proxies.find((p) => p.url === url);
    if (entry) {
      entry.failureCount += 1;
      if (entry.failureCount >= this.maxConsecutiveFailures) {
        entry.healthy = false;
      }
    }
  }

  public getHealthyCount(): number {
    return this.proxies.filter((p) => p.healthy).length;
  }

  public size(): number {
    return this.proxies.length;
  }
}
