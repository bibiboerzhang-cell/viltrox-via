-- Staff tab permissions and owner controls.

ALTER TABLE staff ADD COLUMN IF NOT EXISTS is_owner INTEGER DEFAULT 0;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS email_domain_verified INTEGER DEFAULT 0;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS invited_by_staff_id INTEGER;
ALTER TABLE staff ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_staff_email ON users(email);

UPDATE staff
SET is_owner = 1,
    email_domain_verified = 1
WHERE user_id IN (
    SELECT id FROM users WHERE lower(email) = 'jianboz@viltrox.com'
);
