-- V-KPI v2.1 11-dimension profile payload.
-- The base vkpi_kol_profile_deep table belongs to the v2 deep-scan package.
-- This migration is intentionally safe when that package has not landed yet.

ALTER TABLE IF EXISTS vkpi_kol_profile_deep
    ADD COLUMN IF NOT EXISTS dimensions_11_json TEXT;

