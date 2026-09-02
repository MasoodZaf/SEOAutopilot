from app.deployments.base import (
    DeploymentBlockedError,
    DeploymentRequest,
    DeploymentResult,
)


class ShopifyDeploymentAdapter:
    """Unwired Shopify adapter contract; it must not report an external deployment."""

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
        _ = request
        raise DeploymentBlockedError("shopify_connector_not_implemented")


class WordPressDeploymentAdapter:
    """Unwired WordPress adapter contract; it must not report an external deployment."""

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
        _ = request
        raise DeploymentBlockedError("wordpress_connector_not_implemented")
