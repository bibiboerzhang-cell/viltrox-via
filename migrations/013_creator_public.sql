CREATE TABLE IF NOT EXISTS creator_shop_heroes (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    subtitle TEXT DEFAULT '',
    image_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    badge TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_creator_shop_heroes_user
    ON creator_shop_heroes(user_id, is_active, sort_order);

CREATE TABLE IF NOT EXISTS creator_public_clicks (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    creator_code TEXT NOT NULL,
    click_type TEXT NOT NULL,
    target_url TEXT NOT NULL,
    shop_hero_id TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    ip_hash TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_creator_public_clicks_creator
    ON creator_public_clicks(creator_code, created_at DESC);
