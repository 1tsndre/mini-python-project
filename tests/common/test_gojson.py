import dataclasses
import datetime as dt
import decimal
import uuid

import pytest

from common import gojson
from common.response import Error, Meta, Pagination, Response
from store_service import constant
from store_service.model import (
    AddCartItemRequest,
    Cart,
    CartItem,
    LoginRequest,
    Order,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Product,
    User,
)
from store_service.model.zero import ZERO_TIME

META = Meta(request_id="req-1", timestamp="2026-10-07T03:04:05Z")


def test_error_envelope_omits_data_and_an_empty_field() -> None:
    body = Response(meta=META, errors=[Error(code="NOT_FOUND", message="not found")])

    assert gojson.encode(body) == (
        b'{"meta":{"request_id":"req-1","timestamp":"2026-10-07T03:04:05Z"},'
        b'"errors":[{"code":"NOT_FOUND","message":"not found"}]}\n'
    )


def test_success_envelope_omits_errors_and_keeps_empty_lists() -> None:
    meta = dataclasses.replace(META, pagination=Pagination(current_page=1, per_page=10, total_items=0, total_pages=0))

    assert gojson.marshal(Response(data=[], meta=meta)) == (
        b'{"data":[],"meta":{"request_id":"req-1","timestamp":"2026-10-07T03:04:05Z",'
        b'"pagination":{"current_page":1,"per_page":10,"total_items":0,"total_pages":0}}}'
    )


def test_field_errors_carry_the_field() -> None:
    error = Error(code="VALIDATION_ERROR", field="email", message="is required")

    assert gojson.marshal(error) == b'{"code":"VALIDATION_ERROR","field":"email","message":"is required"}'


def test_html_characters_and_line_separators_are_escaped_like_go() -> None:
    assert gojson.marshal({"name": "<b>Tom & Jerry</b> /x"}) == (
        b'{"name":"\\u003cb\\u003eTom \\u0026 Jerry\\u003c/b\\u003e\\u2028/x"}'
    )
    assert gojson.marshal({"c": "\u0001\t"}) == b'{"c":"\\u0001\\t"}'
    assert gojson.marshal({"b": 2, "a": 1}) == b'{"a":1,"b":2}', "map keys are sorted"


def test_decimals_are_strings_without_trailing_zeros() -> None:
    assert b'"price":"15000.5"' in gojson.marshal(Product(price=decimal.Decimal("15000.50")))
    assert gojson.marshal({"total": decimal.Decimal("20000.00")}) == b'{"total":"20000"}'


def test_timestamps_keep_their_offset_and_drop_trailing_zeros() -> None:
    t = dt.datetime(2026, 10, 7, 10, 11, 12, 120_000, tzinfo=dt.timezone(dt.timedelta(hours=7)))

    assert gojson.marshal({"t": t}) == b'{"t":"2026-10-07T10:11:12.12+07:00"}'
    assert gojson.marshal({"t": t.replace(microsecond=0)}) == b'{"t":"2026-10-07T10:11:12+07:00"}'
    assert gojson.marshal({"t": ZERO_TIME}) == b'{"t":"0001-01-01T00:00:00Z"}'


def test_order_response_shapes_match_go() -> None:
    without_items = gojson.marshal(Order(id=uuid.uuid4(), total_amount=decimal.Decimal(10)).to_response())
    assert b'"items":null' in without_items
    assert b'"payment"' not in without_items

    order = Order(id=uuid.uuid4(), total_amount=decimal.Decimal(10), payment=Payment(amount=decimal.Decimal(10)))
    assert b'"paid_at":null' in gojson.marshal(order.to_response())


def test_enums_are_written_as_their_values() -> None:
    payment = Payment(method=PaymentMethod.MOCK, status=PaymentStatus.SUCCESS, amount=decimal.Decimal(10))
    order = Order(id=uuid.uuid4(), status=constant.OrderStatus.PAID, total_amount=decimal.Decimal(10), payment=payment)
    body = gojson.marshal(order.to_response())
    assert b'"status":"paid"' in body
    assert b'"method":"mock"' in body
    assert b'"status":"success"' in body
    assert b'"role":"seller"' in gojson.marshal(User(role=constant.Role.SELLER).to_response())


def test_cached_models_round_trip_with_their_offset() -> None:
    updated_at = dt.datetime(2026, 10, 7, 10, 0, 0, 5, tzinfo=dt.timezone(dt.timedelta(hours=7)))
    item = CartItem(product_id=uuid.uuid4(), name="Mug", price=decimal.Decimal("9.90"), quantity=2)
    cart = Cart(user_id=uuid.uuid4(), items=[item], updated_at=updated_at)

    cached = gojson.marshal(cart)
    assert cached.startswith(b'{"user_id":')
    assert b'"price":"9.9"' in cached

    restored = gojson.unmarshal(cached, Cart)
    assert restored == cart
    assert restored.updated_at.utcoffset() == dt.timedelta(hours=7)


def test_names_match_case_insensitively_and_unknown_fields_are_ignored() -> None:
    req = gojson.bind(gojson.decode(b'{"EMAIL":"a@b.co","Password":"x","extra":{"y":[1]}}'), LoginRequest)

    assert (req.email, req.password) == ("a@b.co", "x")


def test_missing_and_null_fields_get_zero_values() -> None:
    assert gojson.bind(gojson.decode(b'{"product_id":null}'), AddCartItemRequest) == AddCartItemRequest()
    assert gojson.bind(gojson.decode(b"null"), AddCartItemRequest) == AddCartItemRequest()


def test_only_the_first_value_is_read() -> None:
    assert gojson.bind(gojson.decode(b' {"email":"a"} trailing'), LoginRequest).email == "a"


@pytest.mark.parametrize("body", [b'{"email":123}', b'{"email":true}', b'{"email":{}}', b'{"email":["a"]}'])
def test_a_non_string_for_a_string_field_is_rejected(body: bytes) -> None:
    with pytest.raises(gojson.DecodeError):
        gojson.bind(gojson.decode(body), LoginRequest)


@pytest.mark.parametrize(
    "body",
    [
        b'{"quantity":"2"}',
        b'{"quantity":2.0}',
        b'{"quantity":2.5}',
        b'{"quantity":1e2}',
        b'{"quantity":true}',
        b'{"quantity":99999999999999999999}',
        b'{"quantity":' + b"9" * 5000 + b"}",
    ],
    ids=lambda body: body[:28].decode(),
)
def test_anything_but_an_int64_for_an_integer_field_is_rejected(body: bytes) -> None:
    with pytest.raises(gojson.DecodeError):
        gojson.bind(gojson.decode(body), AddCartItemRequest)


def test_large_integers_fit_like_go_int64() -> None:
    assert gojson.bind(gojson.decode(b'{"quantity":3000000000}'), AddCartItemRequest).quantity == 3_000_000_000


@pytest.mark.parametrize("body", [b"", b"  ", b"{", b"NaN", b'{"quantity":Infinity}', b"\xef\xbb\xbf{}"])
def test_invalid_documents_are_rejected(body: bytes) -> None:
    with pytest.raises(gojson.DecodeError):
        gojson.bind(gojson.decode(body), AddCartItemRequest)


def test_invalid_utf8_and_lone_surrogates_become_replacement_characters() -> None:
    req = gojson.bind(gojson.decode(b'{"email":"a\xffb","password":"\\ud800"}'), LoginRequest)

    assert req.email == "a�b"
    assert req.password == "�"


def test_unmarshal_reads_a_whole_document_like_json_unmarshal() -> None:
    with pytest.raises(gojson.DecodeError):
        gojson.unmarshal(b'{"user_id":"x"} trailing', Cart)
    assert gojson.unmarshal(b"null", Cart) == Cart()
