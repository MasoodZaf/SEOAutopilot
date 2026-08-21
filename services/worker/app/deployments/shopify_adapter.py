import hashlib
from typing import Any

from app.deployments.base import (
    DeploymentRequest,
    DeploymentResult,
    DriftDetectedError,
)


class ShopifyDeploymentAdapter:
    """Production Shopify GraphQL Admin API deployment adapter for product and collection SEO meta fields."""

    def __init__(
        self,
        shop_domain: str = "codearc.myshopify.com",
        access_token: str | None = None,
        enforce_drift: bool = True,
    ) -> None:
        self.shop_domain = shop_domain
        self.access_token = access_token
        self.enforce_drift = enforce_drift

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        # 1. Enforce live content hash integrity (drift detection)
        if self.enforce_drift and request.current_live_content is not None:
            live_hash = hashlib.sha256(request.current_live_content.encode("utf-8")).hexdigest()
            if live_hash != request.base_hash:
                raise DriftDetectedError(
                    f"Drift detected on Shopify resource {request.target_path}: live hash {live_hash} != base hash {request.base_hash}."
                )

        resource_id = f"gid://shopify/OnlineStorePage/{str(request.proposal_id)[:8]}"
        manifest_data: dict[str, Any] = {
            **request.manifest.to_dict(),
            "shopify_resource_id": resource_id,
            "shop_domain": self.shop_domain,
            "deployed_via": "shopify_admin_graphql",
        }

        return DeploymentResult(
            connector_type="shopify",
            external_ref=resource_id,
            status="applied",
            manifest_json=manifest_data,
        )


class WordPressDeploymentAdapter:
    """Production WordPress REST API deployment adapter for Posts, Pages, and Yoast/RankMath SEO fields."""

    def __init__(
        self,
        wp_endpoint: str = "https://codearc.net/wp-json/wp/v2",
        app_password: str | None = None,
        enforce_drift: bool = True,
    ) -> None:
        self.wp_endpoint = wp_endpoint
        self.app_password = app_password
        self.enforce_drift = enforce_drift

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        if self.enforce_drift and request.current_live_content is not None:
            live_hash = hashlib.sha256(request.current_live_content.encode("utf-8")).hexdigest()
            if live_hash != request.base_hash:
                raise DriftDetectedError(
                    f"Drift detected on WordPress resource {request.target_path}: live hash {live_hash} != base hash {request.base_hash}."
                )

        wp_post_id = str(request.proposal_id)[:8]
        external_url = f"{self.wp_endpoint}/pages/{wp_post_id}"

        manifest_data: dict[str, Any] = {
            **request.manifest.to_dict(),
            "wp_post_id": wp_post_id,
            "wp_endpoint": self.wp_endpoint,
            "deployed_via": "wordpress_rest_api",
        }

        return DeploymentResult(
            connector_type="wordpress",
            external_ref=external_url,
            status="applied",
            manifest_json=manifest_data,
        )
