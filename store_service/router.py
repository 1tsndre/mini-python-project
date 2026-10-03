import datetime as dt
from dataclasses import dataclass

from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from common.jwt import JWTManager
from common.response import Error, error_response, success
from store_service import constant, nethttp
from store_service.config import RateConfig
from store_service.handler.auth_handler import AuthHandler
from store_service.handler.cart_handler import CartHandler
from store_service.handler.category_handler import CategoryHandler
from store_service.handler.helpers import answer_errors
from store_service.handler.order_handler import OrderHandler
from store_service.handler.product_handler import ProductHandler
from store_service.handler.review_handler import ReviewHandler
from store_service.handler.store_handler import StoreHandler
from store_service.middleware.auth import auth, require_role
from store_service.middleware.body_limit import MaxBodyBytesMiddleware
from store_service.middleware.clean_path import CleanPathMiddleware
from store_service.middleware.helpers import Handler, build_meta, chain
from store_service.middleware.helpers import Middleware as RouteMiddleware
from store_service.middleware.logging import LoggingMiddleware
from store_service.middleware.rate_limiter import RateLimiter
from store_service.middleware.recovery import RecoveryMiddleware
from store_service.middleware.request_id import RequestIDMiddleware
from store_service.middleware.timeout import TimeoutMiddleware
from store_service.nethttp.fileserver import file_server

RATE_WINDOW = dt.timedelta(minutes=1)


@dataclass
class Handlers:
    auth: AuthHandler
    store: StoreHandler
    category: CategoryHandler
    product: ProductHandler
    cart: CartHandler
    order: OrderHandler
    review: ReviewHandler


def create_app(
    handlers: Handlers,
    jwt_manager: JWTManager,
    redis_client: Redis,
    upload_dir: str,
    max_body_bytes: int,
    request_timeout: dt.timedelta,
    rate_cfg: RateConfig,
) -> Starlette:
    rate_limiter = RateLimiter(redis_client)

    auth_mw = auth(jwt_manager)
    seller_mw = require_role(constant.Role.SELLER)
    buyer_mw = require_role(constant.Role.BUYER)
    admin_mw = require_role(constant.Role.ADMIN)

    login_rate = rate_limiter.limit(rate_cfg.login, RATE_WINDOW, constant.RateLimitKey.LOGIN)
    public_rate = rate_limiter.limit(rate_cfg.public, RATE_WINDOW, constant.RateLimitKey.PUBLIC)
    auth_rate = rate_limiter.limit(rate_cfg.auth, RATE_WINDOW, constant.RateLimitKey.AUTH)

    def route(method: str, path: str, handler: Handler, *middlewares: RouteMiddleware) -> Route:
        return Route(path, chain(answer_errors(handler), *middlewares), methods=[method])

    h = handlers
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/uploads", redirect_to_directory, methods=["GET"]),
        Route("/uploads/{path:path}", file_server("/uploads/", upload_dir, not_found), methods=["GET"]),
        Route("/docs", redirect_to_directory, methods=["GET"]),
        Route("/docs/{path:path}", file_server("/docs/", "./docs", not_found), methods=["GET"]),
        route("POST", "/api/v1/auth/register", h.auth.register, login_rate, public_rate),
        route("POST", "/api/v1/auth/login", h.auth.login, login_rate, public_rate),
        route("POST", "/api/v1/auth/refresh", h.auth.refresh, auth_rate),
        route("POST", "/api/v1/stores", h.store.create_store, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/stores/{id}", h.store.get_store, public_rate),
        route("PUT", "/api/v1/stores/{id}", h.store.update_store, auth_mw, seller_mw, auth_rate),
        route("POST", "/api/v1/stores/{id}/logo", h.store.upload_logo, auth_mw, seller_mw, auth_rate),
        route("POST", "/api/v1/categories", h.category.create_category, auth_mw, admin_mw, auth_rate),
        route("GET", "/api/v1/categories", h.category.get_categories, public_rate),
        route("PUT", "/api/v1/categories/{id}", h.category.update_category, auth_mw, admin_mw, auth_rate),
        route("DELETE", "/api/v1/categories/{id}", h.category.delete_category, auth_mw, admin_mw, auth_rate),
        route("POST", "/api/v1/products", h.product.create_product, auth_mw, seller_mw, auth_rate),
        route("GET", "/api/v1/products", h.product.get_products, public_rate),
        route("GET", "/api/v1/products/{id}", h.product.get_product, public_rate),
        route("PUT", "/api/v1/products/{id}", h.product.update_product, auth_mw, seller_mw, auth_rate),
        route("DELETE", "/api/v1/products/{id}", h.product.delete_product, auth_mw, seller_mw, auth_rate),
        route("POST", "/api/v1/products/{id}/image", h.product.upload_image, auth_mw, seller_mw, auth_rate),
        route("POST", "/api/v1/products/{id}/reviews", h.review.create_review, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/products/{id}/reviews", h.review.get_product_reviews, public_rate),
        route("GET", "/api/v1/cart", h.cart.get_cart, auth_mw, buyer_mw, auth_rate),
        route("POST", "/api/v1/cart/items", h.cart.add_item, auth_mw, buyer_mw, auth_rate),
        route("PUT", "/api/v1/cart/items/{product_id}", h.cart.update_item, auth_mw, buyer_mw, auth_rate),
        route("DELETE", "/api/v1/cart/items/{product_id}", h.cart.remove_item, auth_mw, buyer_mw, auth_rate),
        route("POST", "/api/v1/orders", h.order.checkout, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/orders", h.order.get_orders, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/orders/{id}", h.order.get_order, auth_mw, buyer_mw, auth_rate),
        route("PUT", "/api/v1/orders/{id}/cancel", h.order.cancel_order, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/orders/{id}/payment", h.order.get_order_payment, auth_mw, buyer_mw, auth_rate),
        route("GET", "/api/v1/seller/orders", h.order.get_seller_orders, auth_mw, seller_mw, auth_rate),
        route("PUT", "/api/v1/orders/{id}/status", h.order.update_order_status, auth_mw, seller_mw, auth_rate),
    ]

    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(RequestIDMiddleware),
            Middleware(LoggingMiddleware),
            Middleware(MaxBodyBytesMiddleware, limit=max_body_bytes),
            Middleware(TimeoutMiddleware, timeout=request_timeout),
            Middleware(RecoveryMiddleware),
            Middleware(CleanPathMiddleware),
        ],
        # The Go router has a catch-all route, so an unknown path and a known path with another
        # method both get its JSON 404; it never answers 405.
        exception_handlers={404: not_found_exception, 405: not_found_exception},
    )
    # "/api/v1/products/" is a different route from "/api/v1/products", as in Go.
    app.router.redirect_slashes = False
    return app


async def health(request: Request) -> Response:
    return success(200, {"status": "ok"}, build_meta())


def not_found(request: Request) -> Response:
    return error_response(404, build_meta(), Error(constant.ErrorCode.NOT_FOUND, message="not found"))


async def not_found_exception(request: Request, exc: HTTPException) -> Response:
    return not_found(request)


async def redirect_to_directory(request: Request) -> Response:
    """ "/uploads" and "/docs" redirect to the directory, as Go's router does."""
    target = nethttp.escape_path(request.url.path + "/")
    if request.url.query:
        target += "?" + request.url.query
    return nethttp.redirect(request.method, target, 307)
