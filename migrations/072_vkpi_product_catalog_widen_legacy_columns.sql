-- Existing installs may already have vkpi_products from the earlier cost/SKU
-- catalog, where user-facing fields were short VARCHAR columns. Official
-- product handles, generated SKUs, and full product names can exceed those
-- limits, so widen them before importing source-backed store data.

ALTER TABLE vkpi_products ALTER COLUMN sku TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN category_main TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN category_detail TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN model_name TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN marketing_name TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN status TYPE TEXT;
ALTER TABLE vkpi_products ALTER COLUMN source_file TYPE TEXT;
