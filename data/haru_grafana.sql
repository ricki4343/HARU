USE haru_db;

/* 1. Monthly Spending Trend */
SELECT
  CAST(DATE_FORMAT(date, '%Y-%m-01') AS DATETIME) AS time,
  SUM(amount_absolute) AS total_spent
FROM transactions
WHERE direction = 'expense'
  AND category_id != (SELECT category_id FROM categories WHERE category_name = 'internal')
GROUP BY time
ORDER BY time ASC;

/* 2. Spending by Categories */
SELECT
  c.category_name AS category,
  SUM(t.amount_absolute) AS total_spent
FROM transactions t
JOIN categories c ON t.category_id = c.category_id
WHERE t.direction = 'expense'
  AND c.category_name != 'internal'
  AND DATE_FORMAT(t.date, '%Y-%m') IN (${month})
GROUP BY c.category_name
ORDER BY total_spent DESC;

/* 3. Top 5 Merchant */
SELECT
  counterparty AS "Merchant",
  COUNT(*) AS "Total Transactions",
  CONCAT('€', FORMAT(SUM(amount_absolute), 0, 'de_DE')) AS "Total Amount"
FROM transactions
WHERE direction = 'expense'
  AND category_id != (SELECT category_id FROM categories WHERE category_name = 'internal')
  AND DATE_FORMAT(date, '%Y-%m') IN (${month})
GROUP BY counterparty
ORDER BY SUM(amount_absolute) DESC
LIMIT 5;