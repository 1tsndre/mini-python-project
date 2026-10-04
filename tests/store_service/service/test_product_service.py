import decimal
import uuid
from unittest.mock import ANY

import pytest

from store_service.model import CreateProductRequest, Product, ProductFilter, Store, UpdateProductRequest
from store_service.repository.product_repository import ProductRepository
from store_service.repository.store_repository import StoreRepository
from store_service.service.errors import ConflictError, ForbiddenError, InternalError, NotFoundError, ValidationError
from store_service.service.product_service import ProductService, validate_price, validate_stock
from tests.mocks import Controller
from tests.store_service.service.errors import db_error, foreign_key_violation, not_found

USER_ID = uuid.uuid4()
STORE_ID = uuid.uuid4()
OTHER_STORE_ID = uuid.uuid4()
CATEGORY_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()


def new_service(ctrl: Controller) -> tuple[ProductService, ProductRepository, StoreRepository]:
    product_repo = ctrl.mock(ProductRepository)
    store_repo = ctrl.mock(StoreRepository)
    return ProductService(product_repo, store_repo), product_repo, store_repo


def own_store() -> Store:
    return Store(id=STORE_ID, user_id=USER_ID)


class TestCreateProduct:
    async def test_success(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.create, ANY)

        resp = await svc.create_product(
            USER_ID,
            CreateProductRequest(
                category_id=str(CATEGORY_ID), name="Laptop", description="A nice laptop", price="15000000", stock=10
            ),
        )

        assert resp.name == "Laptop"
        assert resp.store_id == STORE_ID

    async def test_no_store_found(self, ctrl: Controller) -> None:
        svc, _, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="store not found"):
            await svc.create_product(
                USER_ID, CreateProductRequest(category_id=str(CATEGORY_ID), name="Laptop", price="15000000")
            )

    @pytest.mark.parametrize(
        ("category_id", "price", "message"),
        [
            (str(CATEGORY_ID), "not-a-number", "invalid price"),
            ("not-a-uuid", "15000000", "invalid category_id"),
            # Prices must fit the DECIMAL(15,2) column.
            (str(CATEGORY_ID), "99999999999999", "price is too large"),
        ],
        ids=["invalid price", "invalid category_id", "price the column cannot hold"],
    )
    async def test_invalid_request(self, ctrl: Controller, category_id: str, price: str, message: str) -> None:
        svc, _, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())

        with pytest.raises(ValidationError, match=message):
            await svc.create_product(USER_ID, CreateProductRequest(category_id=category_id, name="Laptop", price=price))


class TestGetProducts:
    @pytest.mark.parametrize(
        ("product_filter", "products"),
        [
            (
                ProductFilter(page=1, per_page=10),
                [
                    Product(id=uuid.uuid4(), name="Laptop", price=decimal.Decimal(15000000)),
                    Product(id=uuid.uuid4(), name="Phone", price=decimal.Decimal(5000000)),
                ],
            ),
            (ProductFilter(page=0, per_page=0), []),
        ],
        ids=["with products", "default pagination when zero"],
    )
    async def test_success(self, ctrl: Controller, product_filter: ProductFilter, products: list[Product]) -> None:
        svc, product_repo, _ = new_service(ctrl)
        ctrl.expect(product_repo.find_all, ANY, returns=(products, len(products)))

        resp, total = await svc.get_products(product_filter)

        assert len(resp) == len(products)
        assert total == len(products)
        assert (product_filter.page, product_filter.per_page) != (0, 0)

    async def test_db_error(self, ctrl: Controller) -> None:
        svc, product_repo, _ = new_service(ctrl)
        ctrl.expect(product_repo.find_all, ANY, raises=db_error())

        with pytest.raises(InternalError):
            await svc.get_products(ProductFilter(page=1, per_page=10))


class TestUpdateProduct:
    async def test_stock_is_written_on_its_own(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=Product(id=PRODUCT_ID, store_id=STORE_ID, stock=5))
        ctrl.expect(product_repo.update, ANY)
        ctrl.expect(product_repo.update_stock, PRODUCT_ID, 7)

        resp = await svc.update_product(USER_ID, PRODUCT_ID, UpdateProductRequest(name="Renamed", stock=7))

        assert resp.name == "Renamed"
        assert resp.stock == 7

    async def test_not_product_owner(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=Product(id=PRODUCT_ID, store_id=OTHER_STORE_ID))

        with pytest.raises(ForbiddenError, match="forbidden"):
            await svc.update_product(USER_ID, PRODUCT_ID, UpdateProductRequest(name="Renamed"))


class TestDeleteProduct:
    async def test_success(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=Product(id=PRODUCT_ID, store_id=STORE_ID))
        ctrl.expect(product_repo.delete, PRODUCT_ID)

        await svc.delete_product(USER_ID, PRODUCT_ID)

    async def test_not_product_owner(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=Product(id=PRODUCT_ID, store_id=OTHER_STORE_ID))

        with pytest.raises(ForbiddenError, match="forbidden"):
            await svc.delete_product(USER_ID, PRODUCT_ID)

    async def test_product_not_found(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, raises=not_found())

        with pytest.raises(NotFoundError, match="product not found"):
            await svc.delete_product(USER_ID, PRODUCT_ID)

    async def test_still_referenced_by_orders_or_carts(self, ctrl: Controller) -> None:
        svc, product_repo, store_repo = new_service(ctrl)
        ctrl.expect(store_repo.find_by_user_id, USER_ID, returns=own_store())
        ctrl.expect(product_repo.find_by_id, PRODUCT_ID, returns=Product(id=PRODUCT_ID, store_id=STORE_ID))
        ctrl.expect(product_repo.delete, PRODUCT_ID, raises=foreign_key_violation())

        with pytest.raises(ConflictError, match="in use"):
            await svc.delete_product(USER_ID, PRODUCT_ID)


# Prices and stock must fit the DECIMAL(15,2) and INTEGER columns exactly; otherwise PostgreSQL
# rejects them (a 500) or silently rounds the price.
@pytest.mark.parametrize(
    ("price", "message"),
    [
        ("0", "greater than 0"),
        ("-1", "greater than 0"),
        ("0.01", None),
        ("100.000", None),
        ("9999999999999.99", None),
        ("10000000000000", "too large"),
        ("10.005", "at most 2 decimal places"),
    ],
)
def test_validate_price(price: str, message: str | None) -> None:
    if message is None:
        validate_price(decimal.Decimal(price))
        return
    with pytest.raises(ValidationError, match=message):
        validate_price(decimal.Decimal(price))


def test_validate_stock() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        validate_stock(-1)
    validate_stock(0)
    validate_stock(2**31 - 1)
    with pytest.raises(ValidationError, match="too large"):
        validate_stock(2**31)
