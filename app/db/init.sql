CREATE TABLE IF NOT EXISTS demo_messages (
    id integer PRIMARY KEY,
    message text NOT NULL
);

INSERT INTO demo_messages (id, message)
VALUES (1, 'Hello from PostgreSQL. The demo application is healthy.')
ON CONFLICT (id) DO NOTHING;
