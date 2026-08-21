from uuid import uuid4

import pytest
from app.deployments.base import (
    DeploymentManifest,
    DeploymentRequest,
    DriftDetectedError,
)
from app.deployments.shopify_adapter import (
    ShopifyDeploymentAdapter,
    WordPressDeploymentAdapter,
)


@pytest.mark.asyncio
async def test_shopify_and_wordpress_adapters() -> None:
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

    shopify_res = await shopify.deploy(request)
    assert shopify_res.status == "applied"
    assert shopify_res.connector_type == "shopify"
    assert "gid://shopify/OnlineStorePage/" in shopify_res.external_ref

    wp = WordPressDeploymentAdapter()
    wp_res = await wp.deploy(request)
    assert wp_res.status == "applied"
    assert wp_res.connector_type == "wordpress"
    assert "wp-json/wp/v2/pages/" in wp_res.external_ref

    # Drift error test
    request_drift = DeploymentRequest(
        tenant_id=uuid4(),
        site_id=uuid4(),
        proposal_id=uuid4(),
        target_type="shopify_page",
        target_path="products/cloud-hosting",
        diff_unified="diff",
        base_hash=manifest.base_hash,
        after_content="new",
        idempotency_key="idemp-shopify-2",
        manifest=manifest,
        current_live_content="different live content",
    )
    with pytest.raises(DriftDetectedError):
        await shopify.deploy(request_drift)
