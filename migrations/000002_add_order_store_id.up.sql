ALTER TABLE orders ADD COLUMN store_id UUID REFERENCES stores(id);

UPDATE orders o SET store_id = (
    SELECT p.store_id
    FROM order_items oi
    JOIN products p ON p.id = oi.product_id
    WHERE oi.order_id = o.id
    LIMIT 1
)
WHERE store_id IS NULL;

ALTER TABLE orders ALTER COLUMN store_id SET NOT NULL;

CREATE INDEX idx_orders_store_id ON orders(store_id);
