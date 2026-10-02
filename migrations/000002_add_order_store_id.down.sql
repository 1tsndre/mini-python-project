DROP INDEX IF EXISTS idx_orders_store_id;
ALTER TABLE orders DROP COLUMN IF EXISTS store_id;
