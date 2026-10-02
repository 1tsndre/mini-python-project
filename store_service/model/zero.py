"""Go's zero values, which unset fields of the models and requests start from."""

import datetime as dt
import decimal
import uuid

ZERO_TIME = dt.datetime(1, 1, 1, tzinfo=dt.UTC)
NIL_UUID = uuid.UUID(int=0)
ZERO_DECIMAL = decimal.Decimal(0)
