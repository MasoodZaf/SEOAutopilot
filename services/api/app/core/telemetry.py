from dataclasses import dataclass


@dataclass
class SystemMetricsCollector:
    """Aggregates real-time telemetry metrics for Prometheus scraping."""

    total_proposals: int = 0
    total_approved_proposals: int = 0
    total_deployed_proposals: int = 0
    total_crawls_completed: int = 0
    emergency_freeze_active: int = 0
    outbox_queue_depth: int = 0

    def generate_prometheus_output(self) -> str:
        return f"""# HELP seo_autopilot_proposals_total Total number of generated proposals
# TYPE seo_autopilot_proposals_total counter
seo_autopilot_proposals_total {self.total_proposals}

# HELP seo_autopilot_proposals_approved_total Total approved proposals
# TYPE seo_autopilot_proposals_approved_total counter
seo_autopilot_proposals_approved_total {self.total_approved_proposals}

# HELP seo_autopilot_proposals_deployed_total Total deployed proposals
# TYPE seo_autopilot_proposals_deployed_total counter
seo_autopilot_proposals_deployed_total {self.total_deployed_proposals}

# HELP seo_autopilot_crawls_completed_total Total successfully completed crawls
# TYPE seo_autopilot_crawls_completed_total counter
seo_autopilot_crawls_completed_total {self.total_crawls_completed}

# HELP seo_autopilot_emergency_freeze_status Emergency kill-switch status (1 if active, 0 otherwise)
# TYPE seo_autopilot_emergency_freeze_status gauge
seo_autopilot_emergency_freeze_status {self.emergency_freeze_active}

# HELP seo_autopilot_outbox_queue_depth Pending unpublished outbox events
# TYPE seo_autopilot_outbox_queue_depth gauge
seo_autopilot_outbox_queue_depth {self.outbox_queue_depth}
"""


def format_traceparent_header(trace_id: str, span_id: str = "00f067aa0ba902b7", sampled: bool = True) -> str:
    """Formats a W3C Trace Context traceparent header for distributed OpenTelemetry tracing."""
    clean_trace = trace_id.replace("-", "").lower()[:32].ljust(32, "0")
    flags = "01" if sampled else "00"
    return f"00-{clean_trace}-{span_id}-{flags}"


global_metrics = SystemMetricsCollector()
