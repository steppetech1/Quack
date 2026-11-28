INSERT INTO customers (id, name, city, signed_up) VALUES
    (1, 'Anna', 'Moscow', '2025-01-10'),
    (2, 'Boris', 'Kazan', '2025-02-03'),
    (3, 'Vera', 'Moscow', '2025-03-15'),
    (4, 'Gleb', 'Perm', '2025-03-20'),
    (5, 'Dina', 'Kazan', '2025-05-01'),
    (6, 'Egor', 'Omsk', '2025-06-12');
INSERT INTO products (id, title, category, price) VALUES
    (1, 'Notebook', 'stationery', 3.5),
    (2, 'Pen', 'stationery', 1.2),
    (3, 'Backpack', 'bags', 45.0),
    (4, 'Headphones', 'electronics', 79.9),
    (5, 'USB cable', 'electronics', 6.0),
    (6, 'Lunch box', 'kitchen', 12.5),
    (7, 'Mug', 'kitchen', 8.0);
INSERT INTO orders (id, customer_id, ordered_at, status) VALUES
    (1, 1, '2025-04-01', 'delivered'),
    (2, 1, '2025-06-18', 'delivered'),
    (3, 2, '2025-04-07', 'cancelled'),
    (4, 3, '2025-05-22', 'delivered'),
    (5, 3, '2025-07-02', 'shipped'),
    (6, 4, '2025-07-10', 'new'),
    (7, 5, '2025-07-11', 'delivered'),
    (8, 2, '2025-07-15', 'shipped');
INSERT INTO order_items (order_id, product_id, quantity) VALUES
    (1, 1, 3), (1, 2, 5),
    (2, 4, 1),
    (3, 3, 1),
    (4, 5, 2), (4, 7, 2),
    (5, 1, 1), (5, 6, 1),
    (6, 4, 2),
    (7, 2, 10), (7, 7, 1),
    (8, 3, 1), (8, 5, 1);
