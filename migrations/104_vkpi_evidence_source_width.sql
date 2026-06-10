ALTER TABLE vkpi_kol_video_evidence
  ALTER COLUMN scrape_source TYPE VARCHAR(40),
  ALTER COLUMN metrics_source TYPE VARCHAR(40);

COMMENT ON COLUMN vkpi_kol_video_evidence.scrape_source IS
  'Source enum for evidence metadata collection. Widened for profile crawl sources such as youtube_profile_crawl.';

COMMENT ON COLUMN vkpi_kol_video_evidence.metrics_source IS
  'Source enum for evidence metric collection. Mirrors scrape_source for URL/profile materialized evidence.';
