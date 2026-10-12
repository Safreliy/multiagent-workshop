-- The fixture owner cannot log in. Agents connect as sql_agent, which only has
-- SELECT privileges and is additionally forced into read-only transactions.
CREATE ROLE fixture_owner NOLOGIN;
CREATE ROLE sql_agent LOGIN;

ALTER ROLE sql_agent SET default_transaction_read_only = on;
ALTER ROLE sql_agent SET statement_timeout = '15s';
ALTER ROLE sql_agent SET lock_timeout = '2s';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO sql_agent;

CREATE TABLE customers (
    id          bigint PRIMARY KEY,
    region      text NOT NULL CHECK (region IN ('north', 'south', 'east', 'west')),
    segment     text NOT NULL CHECK (segment IN ('consumer', 'business', 'enterprise')),
    active      boolean NOT NULL,
    created_at  date NOT NULL
);

CREATE TABLE categories (
    id          integer PRIMARY KEY,
    name        text NOT NULL UNIQUE
);

CREATE TABLE products (
    id          bigint PRIMARY KEY,
    category_id integer NOT NULL REFERENCES categories(id),
    sku         text NOT NULL UNIQUE,
    price       numeric(10, 2) NOT NULL CHECK (price > 0),
    active      boolean NOT NULL
);

CREATE TABLE orders (
    id          bigint PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers(id),
    status      text NOT NULL CHECK (status IN ('completed', 'pending', 'cancelled')),
    created_at  timestamp without time zone NOT NULL,
    channel     text NOT NULL CHECK (channel IN ('web', 'store', 'partner'))
);

CREATE TABLE order_items (
    order_id    bigint NOT NULL REFERENCES orders(id),
    line_no     smallint NOT NULL,
    product_id  bigint NOT NULL REFERENCES products(id),
    quantity    smallint NOT NULL CHECK (quantity > 0),
    unit_price  numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (order_id, line_no)
);

-- These indexes are deliberately realistic rather than tailored to the answer.
-- They make the correlated baseline viable while leaving a substantial rewrite
-- opportunity: the same customer order range is scanned three times.
CREATE INDEX orders_customer_created_idx
    ON orders (customer_id, created_at DESC);
CREATE INDEX orders_completed_created_idx
    ON orders (created_at, customer_id)
    WHERE status = 'completed';
CREATE INDEX order_items_product_idx
    ON order_items (product_id, order_id);
CREATE INDEX products_category_idx
    ON products (category_id, id);

ALTER TABLE customers OWNER TO fixture_owner;
ALTER TABLE categories OWNER TO fixture_owner;
ALTER TABLE products OWNER TO fixture_owner;
ALTER TABLE orders OWNER TO fixture_owner;
ALTER TABLE order_items OWNER TO fixture_owner;
ALTER INDEX orders_customer_created_idx OWNER TO fixture_owner;
ALTER INDEX orders_completed_created_idx OWNER TO fixture_owner;
ALTER INDEX order_items_product_idx OWNER TO fixture_owner;
ALTER INDEX products_category_idx OWNER TO fixture_owner;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE fixture_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO sql_agent;
