export type FetchedResource = {
  requestedUrl: string;
  finalUrl: string;
  status: number;
  contentType: string;
  body: string;
  rendered: boolean;
  // What the server sent before a browser ran it. Present only on a rendered
  // resource, where `body` is the DOM after scripts; the two together are the
  // evidence for how much of a page exists without JavaScript.
  serverBody?: string;
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
  // Words in the server's HTML before any script ran; null when the page was
  // not rendered, because then `wordCount` already is that number.
  serverWordCount: number | null;
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
