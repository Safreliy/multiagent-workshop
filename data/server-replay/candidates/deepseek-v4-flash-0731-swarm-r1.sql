WITH qo AS (
    SELECT o.id, o.customer_id, o.created_at
    FROM orders o
    WHERE lower(o.status)='completed' AND o.created_at::date >= DATE '2024-01-01'
),
cap AS (SELECT DISTINCT order_id FROM payments WHERE status='captured'),
order_metrics AS (
    SELECT qo.customer_id,
           count(DISTINCT CASE WHEN cap.order_id IS NOT NULL THEN qo.id END) AS captured_order_count,
           max(qo.created_at) AS last_completed_at
    FROM qo LEFT JOIN cap ON cap.order_id=qo.id
    GROUP BY qo.customer_id
),
revenue AS (
    SELECT qo.customer_id,
           COALESCE(sum((oi.quantity - COALESCE(r.quantity,0))*oi.unit_price),0)::numeric(20,2) AS net_item_revenue
    FROM qo
    JOIN cap ON cap.order_id=qo.id
    JOIN order_items oi ON oi.order_id=qo.id
    LEFT JOIN returns r ON r.order_id=oi.order_id AND r.line_no=oi.line_no
    GROUP BY qo.customer_id
),
promo AS (
    SELECT qo.customer_id,
           COALESCE(sum(op.discount_amount),0)::numeric(18,2) AS active_promotion_discount
    FROM qo
    JOIN order_promotions op ON op.order_id=qo.id
    JOIN promotions pr ON pr.id=op.promotion_id
    WHERE pr.active
    GROUP BY qo.customer_id
),
late AS (
    SELECT qo.customer_id, count(*) AS late_shipment_count
    FROM qo
    JOIN shipments sh ON sh.order_id=qo.id
    WHERE sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days'
    GROUP BY qo.customer_id
),
risky AS (
    SELECT qo.customer_id, count(DISTINCT ps.supplier_id) AS risky_supplier_count
    FROM qo
    JOIN order_items oi ON oi.order_id=qo.id
    JOIN product_suppliers ps ON ps.product_id=oi.product_id
    JOIN suppliers s ON s.id=ps.supplier_id
    WHERE s.risk_tier>=4
    GROUP BY qo.customer_id
)
SELECT c.id, c.region, c.segment,
       COALESCE(om.captured_order_count,0),
       COALESCE(rev.net_item_revenue,0),
       COALESCE(pr.active_promotion_discount,0),
       COALESCE(lt.late_shipment_count,0),
       COALESCE(rk.risky_supplier_count,0),
       om.last_completed_at
FROM customers c
LEFT JOIN order_metrics om ON om.customer_id=c.id
LEFT JOIN revenue rev ON rev.customer_id=c.id
LEFT JOIN promo pr ON pr.customer_id=c.id
LEFT JOIN late lt ON lt.customer_id=c.id
LEFT JOIN risky rk ON rk.customer_id=c.id
WHERE c.active AND EXISTS (
      SELECT 1
      FROM orders o
      JOIN order_items oi ON oi.order_id = o.id
      JOIN products p ON p.id = oi.product_id
      JOIN product_suppliers ps ON ps.product_id = p.id
      JOIN suppliers s ON s.id = ps.supplier_id
      JOIN shipments sh ON sh.order_id = o.id
      WHERE o.customer_id = c.id
        AND lower(o.status) = 'completed'
        AND o.created_at::date >= DATE '2024-01-01'
        AND p.active
        AND s.risk_tier >= 4
        AND (sh.delivered_at IS NULL OR sh.delivered_at > sh.shipped_at + INTERVAL '10 days')
  );