CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    balance DECIMAL(15,2) NOT NULL DEFAULT 100000.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE
);

CREATE TABLE client_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    instrument VARCHAR(20) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type VARCHAR(10) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP')),
    quantity DECIMAL(15,2) NOT NULL,
    limit_price DECIMAL(15,6),
    stop_price DECIMAL(15,6),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'MATCHED', 'SUBMITTED', 'FILLED', 'REJECTED', 'CANCELLED')),
    matched_price DECIMAL(15,6),
    matched_at TIMESTAMP WITH TIME ZONE,
    fill_price DECIMAL(15,6),
    filled_at TIMESTAMP WITH TIME ZONE,
    rejection_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE lp_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_order_id UUID NOT NULL REFERENCES client_orders(id),
    lp_name VARCHAR(50) NOT NULL DEFAULT 'oanda',
    lp_order_id VARCHAR(100),
    instrument VARCHAR(20) NOT NULL,
    side VARCHAR(4) NOT NULL,
    quantity DECIMAL(15,2) NOT NULL,
    submitted_price DECIMAL(15,6),
    fill_price DECIMAL(15,6),
    status VARCHAR(20) NOT NULL DEFAULT 'SUBMITTED'
        CHECK (status IN ('SUBMITTED', 'FILLED', 'REJECTED', 'PARTIALLY_FILLED')),
    rejection_reason TEXT,
    submitted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    filled_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_client_orders_user_id ON client_orders(user_id);
CREATE INDEX idx_client_orders_status ON client_orders(status);
CREATE INDEX idx_client_orders_instrument ON client_orders(instrument);
CREATE INDEX idx_lp_orders_client_order_id ON lp_orders(client_order_id);
CREATE INDEX idx_lp_orders_status ON lp_orders(status);

-- Seed demo user (password: demo123)
-- bcrypt hash for "demo123"
INSERT INTO users (username, password_hash, email, balance)
VALUES ('demo', '$2b$12$iirkyET6qdgd77z8VIoOL.Xtn1KUnl7aat.slCAcoPGgZPM4x8gKO', 'demo@nexusfx.local', 100000.00);
