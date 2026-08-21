export type Site = {
  id: string;
  name: string;
  canonical_origin: string;
  normalized_host: string;
  mode: "observe" | "recommend" | "autopilot";
  status: string;
};

export type Crawl = {
  id: string;
  site_id: string;
  kind: string;
  status: string;
  config_snapshot: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  created_at: string;
};

export const challengeCookie = "seo-autopilot-pilot-challenge";

export type CalibrationReview = {
  id: string;
  accuracy_label: "true_positive" | "false_positive" | "uncertain";
  actionability: "accept" | "edit" | "dismiss" | "defer";
  severity_fit: "appropriate" | "overstated" | "understated" | "uncertain";
  notes: string;
  created_at: string;
};

export type CalibrationItem = {
  id: string;
  calibration_run_id: string;
  opportunity_id: string;
  page_id: string;
  ordinal: number;
  rule_key: string;
  evidence_snapshot: {
    opportunity: {title: string; score: number; confidence: number; risk: string};
    page: {id: string; url: string};
    finding: {rule_key: string; severity: string; confidence: number};
    observation: {
      observed_at: string;
      http_status: number | null;
      final_url: string;
      title: string | null;
      meta_description: string | null;
      h1: string[];
      word_count: number;
      rendered: boolean;
      canonical_url: string | null;
      robots_directives: string[];
    };
  };
  current_review: CalibrationReview | null;
};

export type CalibrationRun = {
  id: string;
  site_id: string;
  status: string;
  strategy: string;
  target_size: number;
  created_at: string;
  items: CalibrationItem[];
  summary: {
    target_size: number;
    reviewed: number;
    true_positive: number;
    false_positive: number;
    uncertain: number;
    precision: number | null;
    actionable: number;
  };
};
