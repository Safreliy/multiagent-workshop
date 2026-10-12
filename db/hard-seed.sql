SET ROLE fixture_owner;
SET synchronous_commit = off;

INSERT INTO suppliers (id, name, country, risk_tier)
SELECT g,
       'supplier-' || lpad(g::text, 3, '0'),
       (ARRAY['US', 'DE', 'PL', 'CN', 'BR', 'IN'])[1 + (g * 7 % 6)],
       1 + (g * 11 % 5)
FROM generate_series(1, 160) AS g;

INSERT INTO product_suppliers (product_id, supplier_id, is_primary, unit_cost, lead_days)
SELECT p.id,
       1 + ((p.id * 37 + slot * 53) % 160),
       slot = 1,
       (3 + ((p.id * 19 + slot * 31) % 30000) / 100.0)::numeric(10, 2),
       (2 + ((p.id + slot * 13) % 45))::smallint
FROM products AS p
CROSS JOIN generate_series(1, 2) AS slot;

INSERT INTO payments (id, order_id, status, amount, paid_at)
SELECT o.id * 2,
       o.id,
       CASE
         WHEN o.status = 'completed' AND o.id % 17 <> 0 THEN 'captured'
         WHEN o.status = 'completed' THEN 'refunded'
         WHEN o.status = 'pending' THEN 'authorized'
         ELSE 'failed'
       END,
       (20 + ((o.id * 47) % 125000) / 100.0)::numeric(12, 2),
       o.created_at + INTERVAL '15 minutes'
FROM orders AS o;

-- Some orders have two captured payments. COUNT(*) through this relation is
-- therefore not interchangeable with COUNT(DISTINCT order_id).
INSERT INTO payments (id, order_id, status, amount, paid_at)
SELECT o.id * 2 + 1,
       o.id,
       CASE WHEN o.status = 'completed' THEN 'captured' ELSE 'failed' END,
       (5 + ((o.id * 29) % 20000) / 100.0)::numeric(12, 2),
       o.created_at + INTERVAL '1 day'
FROM orders AS o
WHERE o.id % 7 = 0;

INSERT INTO shipments (id, order_id, warehouse, shipped_at, delivered_at)
SELECT o.id,
       o.id,
       (ARRAY['ams', 'fra', 'waw', 'iad'])[1 + (o.id * 5 % 4)],
       o.created_at + (1 + o.id % 5) * INTERVAL '1 day',
       CASE
         WHEN o.id % 23 = 0 THEN NULL
         ELSE o.created_at + (2 + o.id % 18) * INTERVAL '1 day'
       END
FROM orders AS o
WHERE o.id % 10 <> 0;

INSERT INTO returns (order_id, line_no, returned_at, quantity, reason)
SELECT oi.order_id,
       oi.line_no,
       o.created_at + (20 + oi.line_no) * INTERVAL '1 day',
       LEAST(oi.quantity, 1 + ((oi.order_id + oi.line_no) % 2))::smallint,
       (ARRAY['damaged', 'wrong-item', 'changed-mind'])[1 + ((oi.order_id + oi.line_no) % 3)]
FROM order_items AS oi
JOIN orders AS o ON o.id = oi.order_id
WHERE (oi.order_id * 3 + oi.line_no * 11) % 37 = 0;

INSERT INTO promotions (id, code, kind, active)
SELECT g,
       'PROMO-' || lpad(g::text, 3, '0'),
       (ARRAY['campaign', 'loyalty', 'partner'])[1 + (g * 5 % 3)],
       g % 9 <> 0
FROM generate_series(1, 60) AS g;

INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
SELECT o.id,
       1 + (o.id * 17 % 60),
       CASE WHEN o.id % 29 = 0 THEN NULL
            ELSE (1 + (o.id * 13 % 2500) / 100.0)::numeric(10, 2)
       END
FROM orders AS o
WHERE o.id % 3 = 0;

INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
SELECT o.id,
       1 + ((o.id * 17 + 7) % 60),
       (2 + (o.id * 7 % 1500) / 100.0)::numeric(10, 2)
FROM orders AS o
WHERE o.id % 11 = 0
  AND 1 + ((o.id * 17 + 7) % 60) <> 1 + (o.id * 17 % 60);

ANALYZE suppliers;
ANALYZE product_suppliers;
ANALYZE payments;
ANALYZE shipments;
ANALYZE returns;
ANALYZE promotions;
ANALYZE order_promotions;

RESET ROLE;

