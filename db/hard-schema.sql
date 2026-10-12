-- Extra relations for the harder v2 task.  They deliberately include several
-- one-to-many joins so that a rewrite must preserve bag semantics carefully.
GRANT CREATE ON SCHEMA public TO fixture_owner;
SET ROLE fixture_owner;

CREATE TABLE benchmark_fixture_metadata (
    fixture_id text PRIMARY KEY,
    created_at timestamp without time zone NOT NULL DEFAULT TIMESTAMP '2026-01-01'
);

CREATE TABLE suppliers (
    id          integer PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    country     text NOT NULL,
    risk_tier   smallint NOT NULL CHECK (risk_tier BETWEEN 1 AND 5)
);

CREATE TABLE product_suppliers (
    product_id  bigint NOT NULL REFERENCES products(id),
    supplier_id integer NOT NULL REFERENCES suppliers(id),
    is_primary  boolean NOT NULL,
    unit_cost   numeric(10, 2) NOT NULL CHECK (unit_cost > 0),
    lead_days   smallint NOT NULL CHECK (lead_days > 0),
    PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE payments (
    id          bigint PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id),
    status      text NOT NULL CHECK (status IN ('captured', 'authorized', 'refunded', 'failed')),
    amount      numeric(12, 2) NOT NULL CHECK (amount >= 0),
    paid_at     timestamp without time zone NOT NULL
);

CREATE TABLE shipments (
    id           bigint PRIMARY KEY,
    order_id     bigint NOT NULL REFERENCES orders(id),
    warehouse    text NOT NULL,
    shipped_at   timestamp without time zone NOT NULL,
    delivered_at timestamp without time zone
);

CREATE TABLE returns (
    order_id    bigint NOT NULL,
    line_no     smallint NOT NULL,
    returned_at timestamp without time zone NOT NULL,
    quantity    smallint NOT NULL CHECK (quantity > 0),
    reason      text NOT NULL,
    PRIMARY KEY (order_id, line_no),
    FOREIGN KEY (order_id, line_no) REFERENCES order_items(order_id, line_no)
);

CREATE TABLE promotions (
    id      integer PRIMARY KEY,
    code    text NOT NULL UNIQUE,
    kind    text NOT NULL CHECK (kind IN ('campaign', 'loyalty', 'partner')),
    active  boolean NOT NULL
);

CREATE TABLE order_promotions (
    order_id        bigint NOT NULL REFERENCES orders(id),
    promotion_id    integer NOT NULL REFERENCES promotions(id),
    discount_amount numeric(10, 2),
    PRIMARY KEY (order_id, promotion_id)
);

-- Useful but intentionally incomplete indexes: the task has real rewrite and
-- index-design opportunities instead of one pre-baked winning index.
CREATE INDEX product_suppliers_supplier_idx
    ON product_suppliers (supplier_id, product_id);
CREATE INDEX payments_order_status_idx
    ON payments (order_id, status);
CREATE INDEX shipments_order_idx
    ON shipments (order_id);
CREATE INDEX returns_order_idx
    ON returns (order_id);
CREATE INDEX order_promotions_order_idx
    ON order_promotions (order_id);

INSERT INTO benchmark_fixture_metadata (fixture_id)
VALUES ('sql-agent-scaling-v2');

GRANT SELECT ON suppliers, product_suppliers, payments, shipments, returns,
    promotions, order_promotions, benchmark_fixture_metadata TO sql_agent;

RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM fixture_owner;
