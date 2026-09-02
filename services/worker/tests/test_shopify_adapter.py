from uuid import uuid4

import pytest
from app.deployments.base import (
    DeploymentBlockedError,
    DeploymentManifest,
    DeploymentRequest,
)
from app.deployments.shopify_adapter import (
    ShopifyDeploymentAdapter,
    WordPressDeploymentAdapter,
)


@pytest.mark.asyncio
async def test_shopify_and_wordpress_adapters_fail_closed_until_wired() -> None:
    manifest = DeploymentManifest(
        tenant_id=str(uuid4()),
        site_id=str(uuid4()),
        proposal_id=str(uuid4()),
        target_path="products/cloud-hosting",
        base_hash="a" * 64,
        proposal_hash="b" * 64,
        author_id=str(uuid4()),
        approver_ids=[str(uuid4())],
        deployed_at="2026-08-21T12:00:00Z",
    )

    shopify = ShopifyDeploymentAdapter(shop_domain="my-shop.myshopify.com")
    request = DeploymentRequest(
        tenant_id=uuid4(),
        site_id=uuid4(),
        proposal_id=uuid4(),
        target_type="shopify_page",
        target_path="products/cloud-hosting",
        diff_unified="diff",
        base_hash=manifest.base_hash,
        after_content="new",
        idempotency_key="idemp-shopify-1",
        manifest=manifest,
        current_live_content=None,
    )

    wp = WordPressDeploymentAdapter()
    with pytest.raises(DeploymentBlockedError, match="shopify_connector_not_implemented"):
        await shopify.deploy(request)
    with pytest.raises(DeploymentBlockedError, match="wordpress_connector_not_implemented"):
        await wp.deploy(request)
