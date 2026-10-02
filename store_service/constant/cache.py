import datetime as dt

KEY_PRODUCT = "product:{}"
KEY_CART = "cart:{}"
KEY_USER = "user:{}"
KEY_RATE_LIMIT = "rate_limit:{}:{}"
KEY_CART_LOCK = "cart_lock:{}"

TTL_PRODUCT = dt.timedelta(minutes=15)
TTL_CART = dt.timedelta(0)  # no expiry
