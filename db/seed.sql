-- All fixture data is derived from generate_series and integer arithmetic. A
-- fresh volume therefore produces byte-for-byte equivalent relational data.
SET ROLE fixture_owner;
SET synchronous_commit = off;

INSERT INTO categories (id, name)
SELECT g, 'category-' || lpad(g::text, 2, '0')
FROM generate_series(1, 24) AS g;

INSERT INTO customers (id, region, segment, active, created_at)
SELECT g,
       (ARRAY['north', 'south', 'east', 'west'])[1 + (g * 7 % 4)],
       (ARRAY['consumer', 'business', 'enterprise'])[1 + (g * 11 % 3)],
       g % 13 <> 0,
       DATE '2018-01-01' + ((g * 17) % 2100)
FROM generate_series(1, 12000) AS g;

INSERT INTO products (id, category_id, sku, price, active)
SELECT g,
       1 + (g * 13 % 24),
       'SKU-' || lpad(g::text, 7, '0'),
       (5 + ((g * 37) % 49500) / 100.0)::numeric(10, 2),
       g % 19 <> 0
FROM generate_series(1, 3000) AS g;

INSERT INTO orders (id, customer_id, status, created_at, channel)
SELECT g,
       1 + (g * 7919 % 12000),
       CASE
         WHEN g % 10 < 7 THEN 'completed'
         WHEN g % 10 < 9 THEN 'pending'
         ELSE 'cancelled'
       END,
       TIMESTAMP '2022-01-01 00:00:00'
         + ((g * 15485863::bigint) % 126230400) * INTERVAL '1 second',
       (ARRAY['web', 'store', 'partner'])[1 + (g * 5 % 3)]
FROM generate_series(1, 240000) AS g;

INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price)
SELECT o.id,
       line_no,
       1 + ((o.id * 31 + line_no * 101) % 3000),
       1 + ((o.id + line_no * 3) % 5),
       (5 + (((o.id * 17 + line_no * 43) % 49500) / 100.0))::numeric(10, 2)
FROM orders AS o
CROSS JOIN LATERAL generate_series(1, 2 + (o.id % 4)::integer) AS line_no;

ANALYZE customers;
ANALYZE categories;
ANALYZE products;
ANALYZE orders;
ANALYZE order_items;

RESET ROLE;

