-- down 170:撤 apify_jobs 租约/幂等/哈希列 + 部分索引。
DROP INDEX IF EXISTS idx_apify_jobs_lease;
ALTER TABLE apify_jobs DROP COLUMN IF EXISTS output_hash;
ALTER TABLE apify_jobs DROP COLUMN IF EXISTS input_hash;
ALTER TABLE apify_jobs DROP COLUMN IF EXISTS idempotency_key;
ALTER TABLE apify_jobs DROP COLUMN IF EXISTS lease_expires_at;
ALTER TABLE apify_jobs DROP COLUMN IF EXISTS lease_owner;
