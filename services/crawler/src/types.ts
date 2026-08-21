export type FetchedResource = {
  requestedUrl: string;
  finalUrl: string;
  status: number;
  contentType: string;
  body: string;
  rendered: boolean;
};

export type LinkObservation = {
  targetUrl: string;
  anchorText: string;
  relValues: string[];
};

export type PageObservation = {
  normalizedUrl: string;
  finalUrl: string;
  status: number;
  title: string | null;
  metaDescription: string | null;
  h1: string[];
  wordCount: number;
  contentHash: string;
  rendered: boolean;
  canonicalUrl: string | null;
  robotsDirectives: string[];
  structuredData: unknown[];
  links: LinkObservation[];
  linkCountTotal: number;
  linksTruncated: boolean;
};

export type FetchResource = (url: URL) => Promise<FetchedResource>;
