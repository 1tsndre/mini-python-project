#!/bin/bash
# =============================================================================
# QA Test Script - mini-python-project
# 84 test case: Health, Auth, Store, Category, Product, Cart, Order (incl. multi-store split + gRPC payment status), Review, Error
# Usage: ./qa_test.sh [BASE_URL]
#
# Login rate limit (10/min) is handled automatically via a file counter.
# The script can be run at any time without manual waiting.
# =============================================================================

BASE_URL="${1:-http://localhost:8080}"
PASS=0
FAIL=0
FAILURES=()

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# Temp files — cleaned up automatically when the script finishes
RESP_FILE=$(mktemp)
RATE_FILE=$(mktemp)
echo "0" > "$RATE_FILE"
trap 'rm -f "$RESP_FILE" "$RATE_FILE"' EXIT

LOGIN_RATE_LIMIT=9   # trigger wait after 9 calls (margin of 1)

# =============================================================================
# Helpers
# =============================================================================

json_get() {
  python3 -c "
import json, sys
d = json.load(sys.stdin)
keys = '$2'.split('.')
for k in keys:
    if isinstance(d, list):
        d = d[int(k)]
    else:
        d = d.get(k, '')
print(d if d is not None else '')
" 2>/dev/null <<< "$1"
}

json_len() {
  python3 -c "
import json, sys
d = json.load(sys.stdin)
keys = '$2'.split('.')
for k in keys:
    if isinstance(d, list):
        d = d[int(k)]
    else:
        d = d.get(k)
print(len(d) if d is not None else 0)
" 2>/dev/null <<< "$1"
}

parse_response() {
  # Input: raw curl output (body + "\n" + http_code)
  RESP_BODY=$(echo "$1" | head -n -1)
  RESP_CODE=$(echo "$1" | tail -n 1)
}

do_get() {
  local path="$1" token="$2"
  if [ -n "$token" ]; then
    curl -s -w "\n%{http_code}" "$BASE_URL$path" -H "Authorization: Bearer $token"
  else
    curl -s -w "\n%{http_code}" "$BASE_URL$path"
  fi
}

do_post() {
  local path="$1" body="$2" token="$3"
  if [ -n "$token" ]; then
    curl -s -w "\n%{http_code}" -X POST "$BASE_URL$path" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $token" \
      --data-binary "$body"
  else
    curl -s -w "\n%{http_code}" -X POST "$BASE_URL$path" \
      -H "Content-Type: application/json" \
      --data-binary "$body"
  fi
}

do_put() {
  local path="$1" body="$2" token="$3"
  curl -s -w "\n%{http_code}" -X PUT "$BASE_URL$path" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $token" \
    --data-binary "$body"
}

do_delete() {
  local path="$1" token="$2"
  curl -s -w "\n%{http_code}" -X DELETE "$BASE_URL$path" \
    -H "Authorization: Bearer $token"
}

wait_order_terminal() {
  local oid="$1" token="$2" st=""
  for _ in $(seq 1 16); do
    sleep 0.5
    parse_response "$(do_get "/api/v1/orders/$oid" "$token")"
    st=$(json_get "$RESP_BODY" "data.status")
    [ "$st" != "pending" ] && break
  done
  echo "$st"
}

# do_auth_post: rate-limit-aware wrapper for /auth/login and /auth/register
# Uses a file counter so the counter persists across subshells.
# Call with redirect: do_auth_post "path" "body" > "$RESP_FILE"
# then: parse_response "$(cat "$RESP_FILE")"
do_auth_post() {
  local path="$1" body="$2"
  local count
  count=$(cat "$RATE_FILE")

  if [ "$count" -ge "$LOGIN_RATE_LIMIT" ]; then
    {
      echo ""
      echo -e "  ${YELLOW}[Rate limit] Waiting 65s for the login bucket to reset...${RESET}"
      for i in $(seq 65 -1 1); do
        printf "\r  ${YELLOW}  Resetting in %2ds...  ${RESET}" "$i"
        sleep 1
      done
      printf "\r  ${GREEN}  Rate limit reset.          ${RESET}\n"
    } >&2
    echo "0" > "$RATE_FILE"
    count=0
  fi

  echo "$((count + 1))" > "$RATE_FILE"
  do_post "$path" "$body"
}

assert() {
  local name="$1" expected_code="$2" actual_code="$3"
  local extra_name="$4" expected_val="$5" actual_val="$6"
  local ok=true

  [ "$actual_code" != "$expected_code" ] && ok=false
  if [ -n "$extra_name" ] && [ "$actual_val" != "$expected_val" ]; then
    ok=false
  fi

  if $ok; then
    echo -e "  ${GREEN}PASS${RESET} $name"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}FAIL${RESET} $name"
    [ "$actual_code" != "$expected_code" ] && \
      echo -e "       HTTP: expected=${BOLD}$expected_code${RESET} got=${BOLD}$actual_code${RESET}"
    [ -n "$extra_name" ] && [ "$actual_val" != "$expected_val" ] && \
      echo -e "       $extra_name: expected=${BOLD}$expected_val${RESET} got=${BOLD}$actual_val${RESET}"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name")
  fi
}

section() {
  echo ""
  echo -e "${CYAN}${BOLD}=== $1 ===${RESET}"
}

abort_if_empty() {
  local val="$1" name="$2"
  if [ -z "$val" ]; then
    echo -e "\n${RED}FATAL: $name is empty — setup failed.${RESET}"
    echo -e "${RED}A rate limit from a previous run is likely still active.${RESET}"
    exit 2
  fi
}

# =============================================================================
# PRE-CHECK: service running & rate limit status
# =============================================================================
echo -e "${BOLD}QA Test Suite - mini-python-project${RESET}"
echo "Base URL  : $BASE_URL"
echo "Started   : $(date '+%H:%M:%S')"
echo ""

RAW=$(do_get "/api/v1/categories")
parse_response "$RAW"
if [ "$RESP_CODE" != "200" ]; then
  echo -e "${RED}ERROR: Service is not running at $BASE_URL${RESET}"
  echo "Run: uv run store-service"
  exit 1
fi
echo -e "${GREEN}Service OK${RESET}"

# Pre-flight: check whether login is already rate-limited before starting
echo -n "Checking rate limit status... "
PREFLIGHT_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" --data-binary '{"email":"x","password":"x"}')
if [ "$PREFLIGHT_CODE" = "429" ]; then
  echo -e "${YELLOW}Rate limited${RESET}"
  echo "1" > "$RATE_FILE"
else
  echo -e "${GREEN}OK (not rate limited)${RESET}"
  echo "1" > "$RATE_FILE"
fi

# =============================================================================
# SETUP: create user, store, category, product
# =============================================================================
echo ""
echo "Preparing fixtures..."
TIMESTAMP=$(date +%s)
BUYER_EMAIL="qa_buyer_${TIMESTAMP}@test.com"
BUYER2_EMAIL="qa_buyer2_${TIMESTAMP}@test.com"
REG_EMAIL="qa_reg_${TIMESTAMP}@test.com"

# Admin login [2]
do_auth_post "/api/v1/auth/login" \
  '{"email":"admin@example.com","password":"admin123"}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
ADMIN_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
abort_if_empty "$ADMIN_TOKEN" "ADMIN_TOKEN"

# Register buyer [3]
do_auth_post "/api/v1/auth/register" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"pass123\",\"name\":\"QA Buyer\"}" > "$RESP_FILE"

# Buyer login, save refresh_token [4]
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
BUYER_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
BUYER_REFRESH=$(json_get "$RESP_BODY" "data.refresh_token")
abort_if_empty "$BUYER_TOKEN" "BUYER_TOKEN"

# Create store (not loginRate)
parse_response "$(do_post "/api/v1/stores" \
  "{\"name\":\"QA Store $TIMESTAMP\",\"description\":\"Test\"}" "$BUYER_TOKEN")"
STORE_ID=$(json_get "$RESP_BODY" "data.id")
abort_if_empty "$STORE_ID" "STORE_ID"

# Re-login → seller token [5]
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
SELLER_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
abort_if_empty "$SELLER_TOKEN" "SELLER_TOKEN"

# Register buyer2 [6]
do_auth_post "/api/v1/auth/register" \
  "{\"email\":\"$BUYER2_EMAIL\",\"password\":\"pass123\",\"name\":\"QA Buyer2\"}" > "$RESP_FILE"

# Buyer2 login [7]
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$BUYER2_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
BUYER2_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
abort_if_empty "$BUYER2_TOKEN" "BUYER2_TOKEN"

# Category & product (not loginRate)
parse_response "$(do_post "/api/v1/categories" \
  "{\"name\":\"QA Cat $TIMESTAMP\"}" "$ADMIN_TOKEN")"
CAT_ID=$(json_get "$RESP_BODY" "data.id")
abort_if_empty "$CAT_ID" "CAT_ID"

parse_response "$(do_post "/api/v1/products" \
  "{\"name\":\"QA Product $TIMESTAMP\",\"description\":\"Test\",\"price\":\"500000.00\",\"stock\":20,\"category_id\":\"$CAT_ID\"}" \
  "$SELLER_TOKEN")"
PROD_ID=$(json_get "$RESP_BODY" "data.id")
abort_if_empty "$PROD_ID" "PROD_ID"

SELLER2_EMAIL="qa_seller2_${TIMESTAMP}@test.com"
do_auth_post "/api/v1/auth/register" \
  "{\"email\":\"$SELLER2_EMAIL\",\"password\":\"pass123\",\"name\":\"QA Seller2\"}" > "$RESP_FILE"
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$SELLER2_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
SELLER2_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
abort_if_empty "$SELLER2_TOKEN" "SELLER2_TOKEN"

parse_response "$(do_post "/api/v1/stores" \
  "{\"name\":\"QA Store2 $TIMESTAMP\",\"description\":\"Test2\"}" "$SELLER2_TOKEN")"
STORE2_ID=$(json_get "$RESP_BODY" "data.id")
abort_if_empty "$STORE2_ID" "STORE2_ID"

do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$SELLER2_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
SELLER2_TOKEN=$(json_get "$RESP_BODY" "data.access_token")

parse_response "$(do_post "/api/v1/products" \
  "{\"name\":\"QA Product2 $TIMESTAMP\",\"description\":\"Test2\",\"price\":\"250000.00\",\"stock\":20,\"category_id\":\"$CAT_ID\"}" \
  "$SELLER2_TOKEN")"
PROD2_ID=$(json_get "$RESP_BODY" "data.id")
abort_if_empty "$PROD2_ID" "PROD2_ID"

COUNT_NOW=$(cat "$RATE_FILE")
echo "  Buyer     : $BUYER_EMAIL"
echo "  Store     : $STORE_ID"
echo "  Store2    : $STORE2_ID"
echo "  Category  : $CAT_ID"
echo "  Product   : $PROD_ID"
echo "  Product2  : $PROD2_ID"
echo "  loginRate : ${COUNT_NOW}/10"

# =============================================================================
# 0. HEALTH
# =============================================================================
section "0. HEALTH"

parse_response "$(do_get "/health")"
STATUS=$(json_get "$RESP_BODY" "data.status")
assert "GET /health → 200 (status: ok)" "200" "$RESP_CODE" "status" "ok" "$STATUS"

# =============================================================================
# 1. AUTH
# =============================================================================
section "1. AUTH"

# Register valid [8]
do_auth_post "/api/v1/auth/register" \
  "{\"email\":\"$REG_EMAIL\",\"password\":\"pass123\",\"name\":\"Reg User\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
assert "Register valid → 201" "201" "$RESP_CODE"

# Register invalid email [9 → RATE_FILE=9, triggers wait on the next call]
do_auth_post "/api/v1/auth/register" \
  '{"email":"not-an-email","password":"pass123","name":"Test"}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Register invalid email → 400" "400" "$RESP_CODE" "field" "email" "$FIELD"

# Register short password [count=9 → triggers wait first]
do_auth_post "/api/v1/auth/register" \
  '{"email":"short@test.com","password":"abc","name":"Test"}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Register short password → 400" "400" "$RESP_CODE" "field" "password" "$FIELD"

# Register all empty
do_auth_post "/api/v1/auth/register" '{}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
ERR_COUNT=$(json_len "$RESP_BODY" "errors")
assert "Register empty → 400 (3 field errors)" "400" "$RESP_CODE" "error count" "3" "$ERR_COUNT"

# Register empty name (valid email+password)
do_auth_post "/api/v1/auth/register" \
  '{"email":"noname@test.com","password":"pass123"}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Register empty name → 400" "400" "$RESP_CODE" "field" "name" "$FIELD"

# Register duplicate
do_auth_post "/api/v1/auth/register" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"pass123\",\"name\":\"Dup\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Register duplicate → 409 CONFLICT" "409" "$RESP_CODE" "error code" "CONFLICT" "$CODE"

# Login unregistered email
do_auth_post "/api/v1/auth/login" \
  '{"email":"notexist@qa.com","password":"pass123"}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Login unregistered email → 401" "401" "$RESP_CODE" "error code" "UNAUTHORIZED" "$CODE"

# Login empty email + password
do_auth_post "/api/v1/auth/login" '{}' > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
ERR_COUNT=$(json_len "$RESP_BODY" "errors")
assert "Login empty → 400 (2 field errors)" "400" "$RESP_CODE" "error count" "2" "$ERR_COUNT"

# Login valid
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"pass123\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
TOKEN_CHECK=$(json_get "$RESP_BODY" "data.access_token")
assert "Login valid → 200 + access_token" "200" "$RESP_CODE" \
  "access_token present" "true" "$([ -n "$TOKEN_CHECK" ] && echo true || echo false)"

# Login wrong password
do_auth_post "/api/v1/auth/login" \
  "{\"email\":\"$BUYER_EMAIL\",\"password\":\"wrongpass\"}" > "$RESP_FILE"
parse_response "$(cat "$RESP_FILE")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Login wrong password → 401" "401" "$RESP_CODE" "error code" "UNAUTHORIZED" "$CODE"

# Refresh token (authRate 120/min — does not use do_auth_post)
parse_response "$(do_post "/api/v1/auth/refresh" "{\"refresh_token\":\"$BUYER_REFRESH\"}")"
NEW_TOKEN=$(json_get "$RESP_BODY" "data.access_token")
assert "Refresh token → 200 + new token" "200" "$RESP_CODE" \
  "access_token present" "true" "$([ -n "$NEW_TOKEN" ] && echo true || echo false)"

# Refresh without token
parse_response "$(do_post "/api/v1/auth/refresh" '{}')"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Refresh without token → 400" "400" "$RESP_CODE" "field" "refresh_token" "$FIELD"

# =============================================================================
# 2. STORE
# =============================================================================
section "2. STORE"

assert "Create store (buyer → seller) → OK" "true" \
  "$([ -n "$STORE_ID" ] && echo true || echo false)"

# Create store without name → 400
parse_response "$(do_post "/api/v1/stores" '{"name":""}' "$BUYER2_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Create store empty name → 400" "400" "$RESP_CODE" "field" "name" "$FIELD"

parse_response "$(do_get "/api/v1/stores/$STORE_ID")"
FETCHED_ID=$(json_get "$RESP_BODY" "data.id")
assert "Get store → 200" "200" "$RESP_CODE" "id match" "$STORE_ID" "$FETCHED_ID"

# Get store invalid UUID → 400
parse_response "$(do_get "/api/v1/stores/not-a-uuid")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Get store invalid UUID → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

# Get store not found → 404
parse_response "$(do_get "/api/v1/stores/00000000-0000-0000-0000-000000000000")"
assert "Get store not found → 404" "404" "$RESP_CODE"

parse_response "$(do_put "/api/v1/stores/$STORE_ID" \
  '{"name":"QA Store Updated","description":"Updated"}' "$SELLER_TOKEN")"
NEW_NAME=$(json_get "$RESP_BODY" "data.name")
assert "Update store (seller) → 200" "200" "$RESP_CODE" "name" "QA Store Updated" "$NEW_NAME"

RAW=$(curl -s -w "\n%{http_code}" -X PUT "$BASE_URL/api/v1/stores/$STORE_ID" \
  -H "Content-Type: application/json" --data-binary '{"name":"Hack"}')
parse_response "$RAW"
assert "Update store without auth → 401" "401" "$RESP_CODE"

# =============================================================================
# 3. CATEGORY
# =============================================================================
section "3. CATEGORY"

assert "Create category (admin) → OK" "true" \
  "$([ -n "$CAT_ID" ] && echo true || echo false)"

# Create category without name → 400
parse_response "$(do_post "/api/v1/categories" '{"name":""}' "$ADMIN_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Create category empty name → 400" "400" "$RESP_CODE" "field" "name" "$FIELD"

parse_response "$(do_post "/api/v1/categories" \
  "{\"name\":\"QA Cat $TIMESTAMP\"}" "$ADMIN_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Create category duplicate → 409" "409" "$RESP_CODE" "error code" "CONFLICT" "$CODE"

parse_response "$(do_post "/api/v1/categories" '{"name":"Unauth Cat"}')"
assert "Create category without auth → 401" "401" "$RESP_CODE"

parse_response "$(do_post "/api/v1/categories" '{"name":"Buyer Cat"}' "$BUYER2_TOKEN")"
assert "Create category (buyer token) → 403" "403" "$RESP_CODE"

parse_response "$(do_get "/api/v1/categories")"
DATA_TYPE=$(python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(type(d.get('data',None)).__name__)" 2>/dev/null <<< "$RESP_BODY")
assert "Get categories → 200 (array, no raw DB error)" "200" "$RESP_CODE" "data type" "list" "$DATA_TYPE"

# Update category invalid UUID → 400
parse_response "$(do_put "/api/v1/categories/not-a-uuid" '{"name":"Test"}' "$ADMIN_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Update category invalid UUID → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

# Update category not found → 404
parse_response "$(do_put "/api/v1/categories/00000000-0000-0000-0000-000000000000" \
  '{"name":"Test"}' "$ADMIN_TOKEN")"
assert "Update category not found → 404" "404" "$RESP_CODE"

# Update category by seller → 403 (middleware role check)
parse_response "$(do_put "/api/v1/categories/$CAT_ID" '{"name":"Hack"}' "$SELLER_TOKEN")"
assert "Update category by seller → 403" "403" "$RESP_CODE"

parse_response "$(do_put "/api/v1/categories/$CAT_ID" \
  "{\"name\":\"QA Cat Upd $TIMESTAMP\"}" "$ADMIN_TOKEN")"
assert "Update category (admin) → 200" "200" "$RESP_CODE"

parse_response "$(do_delete "/api/v1/categories/00000000-0000-0000-0000-000000000000" "$ADMIN_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Delete category not found → 404" "404" "$RESP_CODE" "error code" "NOT_FOUND" "$CODE"

parse_response "$(do_post "/api/v1/categories" \
  "{\"name\":\"QA Del Cat $TIMESTAMP\"}" "$ADMIN_TOKEN")"
DEL_CAT_ID=$(json_get "$RESP_BODY" "data.id")
parse_response "$(do_delete "/api/v1/categories/$DEL_CAT_ID" "$ADMIN_TOKEN")"
MSG=$(json_get "$RESP_BODY" "data.message")
assert "Delete category (admin) → 200" "200" "$RESP_CODE" "message" "category deleted" "$MSG"

# =============================================================================
# 4. PRODUCT
# =============================================================================
section "4. PRODUCT"

assert "Create product (seller) → OK" "true" \
  "$([ -n "$PROD_ID" ] && echo true || echo false)"

# Create product without name → 400
parse_response "$(do_post "/api/v1/products" \
  "{\"price\":\"100.00\",\"stock\":1,\"category_id\":\"$CAT_ID\"}" "$SELLER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Create product without name → 400" "400" "$RESP_CODE" "field" "name" "$FIELD"

# Create product without price → 400
parse_response "$(do_post "/api/v1/products" \
  "{\"name\":\"Test\",\"stock\":1,\"category_id\":\"$CAT_ID\"}" "$SELLER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Create product without price → 400" "400" "$RESP_CODE" "field" "price" "$FIELD"

# Create product without category_id → 400
parse_response "$(do_post "/api/v1/products" \
  '{"name":"Test","price":"100.00","stock":1}' "$SELLER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Create product without category_id → 400" "400" "$RESP_CODE" "field" "category_id" "$FIELD"

parse_response "$(do_post "/api/v1/products" \
  "{\"name\":\"X\",\"price\":\"100.00\",\"stock\":1,\"category_id\":\"$CAT_ID\"}")"
assert "Create product without auth → 401" "401" "$RESP_CODE"

parse_response "$(do_get "/api/v1/products?page=1&per_page=5")"
CURR_PAGE=$(json_get "$RESP_BODY" "meta.pagination.current_page")
assert "Get products list → 200 + pagination" "200" "$RESP_CODE" "current_page" "1" "$CURR_PAGE"

parse_response "$(do_get "/api/v1/products/$PROD_ID")"
FETCHED=$(json_get "$RESP_BODY" "data.id")
assert "Get product detail → 200" "200" "$RESP_CODE" "id match" "$PROD_ID" "$FETCHED"

parse_response "$(do_get "/api/v1/products/00000000-0000-0000-0000-000000000000")"
assert "Get product not found → 404" "404" "$RESP_CODE"

parse_response "$(do_get "/api/v1/products?search=QA%20Product")"
assert "Search product → 200" "200" "$RESP_CODE"

# Filter by category_id → 200
parse_response "$(do_get "/api/v1/products?category_id=$CAT_ID")"
assert "Filter product by category_id → 200" "200" "$RESP_CODE"

parse_response "$(do_put "/api/v1/products/$PROD_ID" \
  "{\"name\":\"QA Product Updated\",\"price\":\"450000.00\",\"stock\":15,\"category_id\":\"$CAT_ID\"}" \
  "$SELLER_TOKEN")"
NEW_PRICE=$(json_get "$RESP_BODY" "data.price")
assert "Update product (seller) → 200" "200" "$RESP_CODE" "price" "450000" "$NEW_PRICE"

parse_response "$(do_post "/api/v1/products" \
  "{\"name\":\"QA Del Prod $TIMESTAMP\",\"description\":\"Del\",\"price\":\"100.00\",\"stock\":1,\"category_id\":\"$CAT_ID\"}" \
  "$SELLER_TOKEN")"
DEL_PROD_ID=$(json_get "$RESP_BODY" "data.id")
parse_response "$(do_delete "/api/v1/products/$DEL_PROD_ID" "$SELLER_TOKEN")"
MSG=$(json_get "$RESP_BODY" "data.message")
assert "Delete product (seller) → 200" "200" "$RESP_CODE" "message" "product deleted" "$MSG"

# =============================================================================
# 5. CART
# =============================================================================
section "5. CART"

parse_response "$(do_get "/api/v1/cart" "$SELLER_TOKEN")"
assert "Seller access cart → 403" "403" "$RESP_CODE"

# Add item quantity 0 → 400
parse_response "$(do_post "/api/v1/cart/items" \
  "{\"product_id\":\"$PROD_ID\",\"quantity\":0}" "$BUYER2_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Add cart item quantity 0 → 400" "400" "$RESP_CODE" "field" "quantity" "$FIELD"

# Add item negative quantity → 400
parse_response "$(do_post "/api/v1/cart/items" \
  "{\"product_id\":\"$PROD_ID\",\"quantity\":-1}" "$BUYER2_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Add cart item negative quantity → 400" "400" "$RESP_CODE" "field" "quantity" "$FIELD"

parse_response "$(do_post "/api/v1/cart/items" \
  "{\"product_id\":\"$PROD_ID\",\"quantity\":2}" "$BUYER2_TOKEN")"
QTY=$(json_get "$RESP_BODY" "data.items.0.quantity")
assert "Add item to cart → 200" "200" "$RESP_CODE" "quantity" "2" "$QTY"

parse_response "$(do_get "/api/v1/cart" "$BUYER2_TOKEN")"
ITEM_COUNT=$(json_len "$RESP_BODY" "data.items")
assert "Get cart → 200 (1 item)" "200" "$RESP_CODE" "item count" "1" "$ITEM_COUNT"

parse_response "$(do_put "/api/v1/cart/items/$PROD_ID" '{"quantity":3}' "$BUYER2_TOKEN")"
NEW_QTY=$(json_get "$RESP_BODY" "data.items.0.quantity")
assert "Update cart quantity → 200" "200" "$RESP_CODE" "quantity" "3" "$NEW_QTY"

# Update cart quantity 0 → 400
parse_response "$(do_put "/api/v1/cart/items/$PROD_ID" '{"quantity":0}' "$BUYER2_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Update cart item quantity 0 → 400" "400" "$RESP_CODE" "field" "quantity" "$FIELD"

parse_response "$(do_delete "/api/v1/cart/items/$PROD_ID" "$BUYER2_TOKEN")"
AFTER_COUNT=$(json_len "$RESP_BODY" "data.items")
assert "Remove cart item → 200 (empty cart)" "200" "$RESP_CODE" "items empty" "0" "$AFTER_COUNT"

parse_response "$(do_get "/api/v1/cart" "$BUYER2_TOKEN")"
UPDATED_AT=$(json_get "$RESP_BODY" "data.updated_at")
IS_ZERO="false"
echo "$UPDATED_AT" | grep -q "0001-01-01" && IS_ZERO="true"
assert "Empty cart updated_at is not zero value" "200" "$RESP_CODE" "is zero" "false" "$IS_ZERO"

# =============================================================================
# 6. ORDER
# =============================================================================
section "6. ORDER"

do_post "/api/v1/cart/items" \
  "{\"product_id\":\"$PROD_ID\",\"quantity\":1}" "$BUYER2_TOKEN" > /dev/null

parse_response "$(do_post "/api/v1/orders" '{}' "$BUYER2_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Checkout without shipping_address → 400" "400" "$RESP_CODE" "field" "shipping_address" "$FIELD"

parse_response "$(do_post "/api/v1/orders" \
  '{"shipping_address":"123 QA Test St, Jakarta"}' "$BUYER2_TOKEN")"
ORDER_ID=$(json_get "$RESP_BODY" "data.0.id")
ORDER_ADDR=$(json_get "$RESP_BODY" "data.0.shipping_address")
assert "Checkout valid → 201 + shipping_address" "201" "$RESP_CODE" \
  "shipping_address" "123 QA Test St, Jakarta" "$ORDER_ADDR"

# Wait for the NSQ pipeline. Mock payment succeeds 90% of the time; if this order
# happens to fail (becomes 'cancelled'), create a new order and retry until 'paid'
# so the status-flow tests below are deterministic.
echo -e "  ${YELLOW}Waiting for the NSQ payment pipeline...${RESET}"
STATUS=$(wait_order_terminal "$ORDER_ID" "$BUYER2_TOKEN")
RETRY=0
while [ "$STATUS" = "cancelled" ] && [ "$RETRY" -lt 4 ]; do
  RETRY=$((RETRY + 1))
  do_post "/api/v1/cart/items" "{\"product_id\":\"$PROD_ID\",\"quantity\":1}" "$BUYER2_TOKEN" > /dev/null
  parse_response "$(do_post "/api/v1/orders" '{"shipping_address":"123 QA Test St, Jakarta"}' "$BUYER2_TOKEN")"
  ORDER_ID=$(json_get "$RESP_BODY" "data.0.id")
  STATUS=$(wait_order_terminal "$ORDER_ID" "$BUYER2_TOKEN")
done
# Final GET in the parent shell so RESP_CODE/RESP_BODY are correct for the assert
parse_response "$(do_get "/api/v1/orders/$ORDER_ID" "$BUYER2_TOKEN")"
STATUS=$(json_get "$RESP_BODY" "data.status")
assert "Order auto-paid via NSQ" "200" "$RESP_CODE" "status" "paid" "$STATUS"

parse_response "$(do_get "/api/v1/orders/$ORDER_ID/payment" "$BUYER2_TOKEN")"
PAY_STATUS=$(json_get "$RESP_BODY" "data.status")
assert "Get order payment status (gRPC) → 200 success" "200" "$RESP_CODE" "status" "success" "$PAY_STATUS"

parse_response "$(do_get "/api/v1/orders/not-a-uuid/payment" "$BUYER2_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Get payment status invalid UUID → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

parse_response "$(do_get "/api/v1/orders" "$BUYER2_TOKEN")"
ORDER_COUNT=$(json_len "$RESP_BODY" "data")
assert "List orders (buyer) → 200" "200" "$RESP_CODE" \
  "has order" "true" "$([ "${ORDER_COUNT:-0}" -gt 0 ] && echo true || echo false)"

parse_response "$(do_get "/api/v1/seller/orders" "$SELLER_TOKEN")"
assert "List seller orders → 200" "200" "$RESP_CODE"

parse_response "$(do_put "/api/v1/orders/$ORDER_ID/status" '{"status":"processing"}' "$SELLER_TOKEN")"
assert "Update order status → processing" "200" "$RESP_CODE"

parse_response "$(do_put "/api/v1/orders/$ORDER_ID/status" '{"status":"shipping"}' "$SELLER_TOKEN")"
assert "Update order status → shipping" "200" "$RESP_CODE"

# Update status without status field → 400
parse_response "$(do_put "/api/v1/orders/$ORDER_ID/status" '{}' "$SELLER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Update order status empty field → 400" "400" "$RESP_CODE" "field" "status" "$FIELD"

parse_response "$(do_put "/api/v1/orders/$ORDER_ID/cancel" '' "$BUYER2_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Cancel order (shipping) → 400 INVALID_STATUS" "400" "$RESP_CODE" \
  "error code" "INVALID_STATUS" "$CODE"

do_post "/api/v1/cart/items" \
  "{\"product_id\":\"$PROD_ID\",\"quantity\":1}" "$BUYER2_TOKEN" > /dev/null
parse_response "$(do_post "/api/v1/orders" '{"shipping_address":"1 Cancel Test St"}' "$BUYER2_TOKEN")"
ORDER2_ID=$(json_get "$RESP_BODY" "data.0.id")
parse_response "$(do_put "/api/v1/orders/$ORDER2_ID/cancel" '' "$BUYER2_TOKEN")"
MSG=$(json_get "$RESP_BODY" "data.message")
assert "Cancel order (pending) → 200" "200" "$RESP_CODE" "message" "order cancelled" "$MSG"

# Checkout empty cart (BUYER2 cart already empty after ORDER2 checkout) → 400
parse_response "$(do_post "/api/v1/orders" \
  '{"shipping_address":"1 Empty Cart St"}' "$BUYER2_TOKEN")"
assert "Checkout empty cart → 400" "400" "$RESP_CODE"

# Get order invalid UUID → 400
parse_response "$(do_get "/api/v1/orders/not-a-uuid" "$BUYER2_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Get order invalid UUID → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

# Update status invalid transition (ORDER2 cancelled → processing) → 400
parse_response "$(do_put "/api/v1/orders/$ORDER2_ID/status" '{"status":"processing"}' "$SELLER_TOKEN")"
assert "Update order status invalid transition → 400" "400" "$RESP_CODE"

# Cancel order invalid UUID → 400
parse_response "$(do_put "/api/v1/orders/not-a-uuid/cancel" '' "$BUYER2_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Cancel order invalid UUID → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

do_post "/api/v1/cart/items" "{\"product_id\":\"$PROD_ID\",\"quantity\":1}" "$BUYER2_TOKEN" > /dev/null
do_post "/api/v1/cart/items" "{\"product_id\":\"$PROD2_ID\",\"quantity\":1}" "$BUYER2_TOKEN" > /dev/null
parse_response "$(do_post "/api/v1/orders" '{"shipping_address":"1 Multi Store St"}' "$BUYER2_TOKEN")"
MULTI_OK=$(python3 -c "
import json, sys
d = json.load(sys.stdin).get('data')
ok = (isinstance(d, list) and len(d) == 2
      and all(len(o.get('items', [])) == 1 for o in d)
      and {o['store_id'] for o in d} == {'$STORE_ID', '$STORE2_ID'})
print('true' if ok else 'false')
" 2>/dev/null <<< "$RESP_BODY")
assert "Checkout multi-store → 201 + 2 orders (1 per store)" "201" "$RESP_CODE" "split correct" "true" "$MULTI_OK"

parse_response "$(do_get "/api/v1/seller/orders" "$SELLER2_TOKEN")"
SELLER2_ISO=$(python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', [])
prods = [i['product_id'] for o in d for i in o.get('items', [])]
ok = (all(o['store_id'] == '$STORE2_ID' for o in d)
      and '$PROD2_ID' in prods and '$PROD_ID' not in prods)
print('true' if ok else 'false')
" 2>/dev/null <<< "$RESP_BODY")
assert "Seller2 /seller/orders → only its own store items" "200" "$RESP_CODE" "isolation" "true" "$SELLER2_ISO"

# =============================================================================
# 7. REVIEW
# =============================================================================
section "7. REVIEW"

do_put "/api/v1/orders/$ORDER_ID/status" '{"status":"shipped"}' "$SELLER_TOKEN" > /dev/null

# Review rating 0 → 400 (handler validation: rating < 1)
parse_response "$(do_post "/api/v1/products/$PROD_ID/reviews" '{"rating":0}' "$BUYER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Review rating 0 → 400" "400" "$RESP_CODE" "field" "rating" "$FIELD"

parse_response "$(do_post "/api/v1/products/$PROD_ID/reviews" '{"rating":5}' "$BUYER_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Review without purchase → 403" "403" "$RESP_CODE" "error code" "FORBIDDEN" "$CODE"

parse_response "$(do_post "/api/v1/products/$PROD_ID/reviews" \
  '{"rating":4,"comment":"Good product"}' "$BUYER2_TOKEN")"
RATING=$(json_get "$RESP_BODY" "data.rating")
assert "Review valid (after shipped) → 201" "201" "$RESP_CODE" "rating" "4" "$RATING"

parse_response "$(do_post "/api/v1/products/$PROD_ID/reviews" '{"rating":3}' "$BUYER2_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Review duplicate → 409 CONFLICT" "409" "$RESP_CODE" "error code" "CONFLICT" "$CODE"

parse_response "$(do_post "/api/v1/products/$PROD_ID/reviews" '{"rating":6}' "$BUYER_TOKEN")"
FIELD=$(json_get "$RESP_BODY" "errors.0.field")
assert "Review rating > 5 → 400" "400" "$RESP_CODE" "field" "rating" "$FIELD"

parse_response "$(do_get "/api/v1/products/$PROD_ID/reviews")"
TOTAL=$(json_get "$RESP_BODY" "meta.pagination.total_items")
assert "List reviews → 200 + pagination" "200" "$RESP_CODE" \
  "total >= 1" "true" "$([ "${TOTAL:-0}" -ge 1 ] && echo true || echo false)"

# =============================================================================
# 8. ERROR CASES
# =============================================================================
section "8. ERROR CASES"

parse_response "$(do_get "/api/v1/endpoint-does-not-exist")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "404 unknown endpoint → JSON NOT_FOUND" "404" "$RESP_CODE" "error code" "NOT_FOUND" "$CODE"

parse_response "$(do_get "/api/v1/products/not-a-uuid")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Invalid UUID path param (product) → 400" "400" "$RESP_CODE" "error code" "VALIDATION_ERROR" "$CODE"

RAW=$(curl -s -w "\n%{http_code}" "$BASE_URL/api/v1/cart" \
  -H "Authorization: Bearer invalid.token.here")
parse_response "$RAW"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Token invalid → 401 UNAUTHORIZED" "401" "$RESP_CODE" "error code" "UNAUTHORIZED" "$CODE"

RAW=$(curl -s -w "\n%{http_code}" "$BASE_URL/api/v1/cart" -H "Authorization: justtoken")
parse_response "$RAW"
assert "Bad Authorization format → 401" "401" "$RESP_CODE"

parse_response "$(do_get "/api/v1/cart" "$SELLER_TOKEN")"
CODE=$(json_get "$RESP_BODY" "errors.0.code")
assert "Seller access buyer endpoint → 403" "403" "$RESP_CODE" "error code" "FORBIDDEN" "$CODE"

# Body not JSON — send to /auth/login (use do_auth_post to stay rate-limit aware)
do_auth_post "/api/v1/auth/login" 'not json' > "$RESP_FILE" 2>/dev/null
parse_response "$(cat "$RESP_FILE")"
assert "Body not JSON → 400" "400" "$RESP_CODE"

# Rate limit test — ensure we are within a sufficient window
echo -e "  ${YELLOW}Rate limit test (11 requests to /login)...${RESET}"
LAST_CODE=""
for i in $(seq 1 11); do
  LAST_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/api/v1/auth/login" \
    -H "Content-Type: application/json" --data-binary '{"email":"x@x.com","password":"wrong"}')
  printf "."
done
echo ""
assert "Rate limit login (11th) → 429" "429" "$LAST_CODE"

# =============================================================================
# SUMMARY
# =============================================================================
TOTAL=$((PASS + FAIL))
echo ""
echo -e "${BOLD}============================================${RESET}"
echo -e "${BOLD}QA TESTING RESULTS${RESET}"
echo -e "--------------------------------------------"
printf "  Total   : %d\n" "$TOTAL"
echo -e "  ${GREEN}Pass    : $PASS${RESET}"
if [ $FAIL -gt 0 ]; then
  echo -e "  ${RED}Fail    : $FAIL${RESET}"
  echo ""
  echo -e "${RED}${BOLD}FAILED TESTS:${RESET}"
  for f in "${FAILURES[@]}"; do
    echo -e "  ${RED}✗${RESET} $f"
  done
else
  echo -e "  ${GREEN}Fail    : 0${RESET}"
  echo ""
  echo -e "  ${GREEN}${BOLD}All test cases PASS.${RESET}"
fi
echo "  Finished : $(date '+%H:%M:%S')"
echo -e "${BOLD}============================================${RESET}"

[ $FAIL -eq 0 ] && exit 0 || exit 1
