import uuid
from typing import Any

import pytest

from store_service import constant
from store_service.handler.auth_handler import AuthHandler
from store_service.handler.category_handler import CategoryHandler
from store_service.handler.helpers import exceeds_varchar
from store_service.handler.product_handler import ProductHandler
from store_service.handler.store_handler import StoreHandler
from store_service.model import ProductFilter, ProductResponse
from tests.store_service.handler.helpers import call, decode_error, json_body, make_request

LONG = "a" * (constant.MAX_VARCHAR_LENGTH + 1)
USER_ID = uuid.uuid4()
PATH_ID = {"id": str(uuid.uuid4())}


def cases() -> list[Any]:
    # The services are None: a request that reached one would fail the test.
    auth, store = AuthHandler(None), StoreHandler(None, None)
    category, product = CategoryHandler(None), ProductHandler(None, None)

    def register(**fields: str) -> str:
        return json_body({"email": "a@example.com", "password": "secret1", "name": "A", **fields})

    return [
        pytest.param(
            auth.register, make_request("POST", "/", register(email=LONG + "@example.com")),
            "email", "maximum 255 characters", id="register - email too long",
        ),
        pytest.param(
            auth.register, make_request("POST", "/", register(name=LONG)),
            "name", "maximum 255 characters", id="register - name too long",
        ),
        pytest.param(
            auth.register, make_request("POST", "/", register(password="p" * (constant.MAX_PASSWORD_BYTES + 1))),
            "password", "maximum 72 bytes", id="register - password longer than bcrypt accepts",
        ),
        pytest.param(
            store.create_store, make_request("POST", "/", json_body({"name": LONG}), user_id=USER_ID),
            "name", "maximum 255 characters", id="create store - name too long",
        ),
        pytest.param(
            store.update_store,
            make_request("PUT", "/", json_body({"name": LONG}), user_id=USER_ID, path_params=PATH_ID),
            "name", "maximum 255 characters", id="update store - name too long",
        ),
        pytest.param(
            category.create_category, make_request("POST", "/", json_body({"name": LONG})),
            "name", "maximum 255 characters", id="create category - name too long",
        ),
        pytest.param(
            category.update_category, make_request("PUT", "/", json_body({"name": LONG}), path_params=PATH_ID),
            "name", "maximum 255 characters", id="update category - name too long",
        ),
        pytest.param(
            product.create_product,
            make_request(
                "POST",
                "/",
                json_body({"name": LONG, "price": "10.00", "stock": 1, "category_id": str(uuid.uuid4())}),
                user_id=USER_ID,
            ),
            "name", "maximum 255 characters", id="create product - name too long",
        ),
        pytest.param(
            product.update_product,
            make_request("PUT", "/", json_body({"name": LONG}), user_id=USER_ID, path_params=PATH_ID),
            "name", "maximum 255 characters", id="update product - name too long",
        ),
        pytest.param(
            product.get_products, make_request("GET", "/api/v1/products?category_id=abc"),
            "category_id", "must be a valid UUID", id="list products - category_id is not a UUID",
        ),
        pytest.param(
            product.get_products, make_request("GET", "/api/v1/products?store_id=1%27%20or%201%3D1"),
            "store_id", "must be a valid UUID", id="list products - store_id is not a UUID",
        ),
    ]  # fmt: skip


# Inputs that PostgreSQL or bcrypt would reject must fail validation with a 400 before reaching the service.
@pytest.mark.parametrize(("handler", "request_", "field", "message"), cases())
async def test_handlers_reject_input_the_database_cannot_store(
    handler: Any, request_: Any, field: str, message: str
) -> None:
    response = await call(handler, request_)

    assert response.status_code == 400
    error = decode_error(response)
    assert error["code"] == constant.ErrorCode.VALIDATION
    assert error["field"] == field
    assert error["message"] == message


def test_exceeds_varchar_counts_characters_not_bytes() -> None:
    # "é" is two bytes; PostgreSQL's VARCHAR(255) limit is in characters.
    assert not exceeds_varchar("é" * constant.MAX_VARCHAR_LENGTH)
    assert exceeds_varchar("é" * (constant.MAX_VARCHAR_LENGTH + 1))


class CaptureProductService:
    def __init__(self) -> None:
        self.filter: ProductFilter | None = None

    async def get_products(self, product_filter: ProductFilter) -> tuple[list[ProductResponse], int]:
        self.filter = product_filter
        return [], 0


async def test_get_products_passes_id_filters_in_canonical_form() -> None:
    category_id = uuid.uuid4()
    svc = CaptureProductService()

    # Upper case and braces parse as a UUID; the query gets the canonical form.
    request = make_request("GET", f"/api/v1/products?category_id=%7B{str(category_id).upper()}%7D")
    response = await call(ProductHandler(svc, None).get_products, request)

    assert response.status_code == 200
    assert svc.filter is not None
    assert svc.filter.category_id == str(category_id)
    assert svc.filter.store_id == ""
