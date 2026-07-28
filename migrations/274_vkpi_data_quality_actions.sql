-- 274: move data-quality action schema ownership out of request handlers.
--
-- GET /api/admin/vkpi/data-quality must stay read-only. The former runtime
-- compatibility helper issued CREATE TABLE/INDEX and COMMIT before reads.
-- PostgreSQL now receives this schema only through the migration runner.
--
-- This table can already exist on upgraded databases because the legacy helper
-- created it at runtime. IF NOT EXISTS preserves that ledger; the validation
-- block below refuses to record migration 274 when the existing relation is
-- not exactly compatible with the application contract.
--
-- The migration runner owns the transaction; no BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_data_quality_actions (
  id BIGSERIAL PRIMARY KEY,
  issue_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT DEFAULT '',
  staff_id BIGINT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $migration_274_columns$
DECLARE
  target_table REGCLASS := to_regclass('vkpi_data_quality_actions');
  actual_columns TEXT[];
  mismatch_detail TEXT;
  id_default TEXT;
  reason_default TEXT;
  metadata_default TEXT;
  created_default TEXT;
  owned_id_sequence REGCLASS;
  default_id_sequence REGCLASS;
  constraint_count INTEGER;
  primary_key_count INTEGER;
  primary_key_columns TEXT[];
  extra_unique_index_count INTEGER;
BEGIN
  IF target_table IS NULL THEN
    RAISE EXCEPTION
      'migration 274 invariant failed: vkpi_data_quality_actions is missing';
  END IF;

  SELECT array_agg(attribute.attname::TEXT ORDER BY attribute.attnum)
    INTO actual_columns
  FROM pg_attribute AS attribute
  WHERE attribute.attrelid = target_table
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  IF actual_columns IS DISTINCT FROM ARRAY[
    'id',
    'issue_id',
    'action',
    'reason',
    'staff_id',
    'metadata_json',
    'created_at'
  ]::TEXT[] THEN
    RAISE EXCEPTION
      'migration 274 incompatible columns: expected seven canonical columns, got %',
      actual_columns;
  END IF;

  SELECT string_agg(
           format(
             '%s(type=%s, not_null=%s)',
             expected.column_name,
             COALESCE(pg_catalog.format_type(actual.atttypid, actual.atttypmod), 'missing'),
             COALESCE(actual.attnotnull::TEXT, 'missing')
           ),
           ', ' ORDER BY expected.ordinal
         )
    INTO mismatch_detail
  FROM (
    VALUES
      (1, 'id',            'bigint',                   TRUE),
      (2, 'issue_id',      'text',                     TRUE),
      (3, 'action',        'text',                     TRUE),
      (4, 'reason',        'text',                     FALSE),
      (5, 'staff_id',      'bigint',                   FALSE),
      (6, 'metadata_json', 'text',                     TRUE),
      (7, 'created_at',    'timestamp with time zone', TRUE)
  ) AS expected(ordinal, column_name, type_name, is_not_null)
  LEFT JOIN pg_attribute AS actual
    ON actual.attrelid = target_table
   AND actual.attname = expected.column_name
   AND actual.attnum > 0
   AND NOT actual.attisdropped
  WHERE actual.attname IS NULL
     OR pg_catalog.format_type(actual.atttypid, actual.atttypmod)
          IS DISTINCT FROM expected.type_name
     OR actual.attnotnull IS DISTINCT FROM expected.is_not_null;

  IF mismatch_detail IS NOT NULL THEN
    RAISE EXCEPTION
      'migration 274 incompatible column contract: %',
      mismatch_detail;
  END IF;

  SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
    INTO id_default
  FROM pg_attribute AS attribute
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE attribute.attrelid = target_table
    AND attribute.attname = 'id'
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  SELECT to_regclass(pg_get_serial_sequence(target_table::TEXT, 'id'))
    INTO owned_id_sequence;

  SELECT dependency.refobjid::REGCLASS
    INTO default_id_sequence
  FROM pg_attribute AS attribute
  JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  JOIN pg_depend AS dependency
    ON dependency.classid = 'pg_attrdef'::REGCLASS
   AND dependency.objid = default_value.oid
   AND dependency.refclassid = 'pg_class'::REGCLASS
   AND dependency.deptype = 'n'
  JOIN pg_class AS referenced_relation
    ON referenced_relation.oid = dependency.refobjid
   AND referenced_relation.relkind = 'S'
  WHERE attribute.attrelid = target_table
    AND attribute.attname = 'id'
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
    INTO reason_default
  FROM pg_attribute AS attribute
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE attribute.attrelid = target_table
    AND attribute.attname = 'reason'
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
    INTO metadata_default
  FROM pg_attribute AS attribute
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE attribute.attrelid = target_table
    AND attribute.attname = 'metadata_json'
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
    INTO created_default
  FROM pg_attribute AS attribute
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE attribute.attrelid = target_table
    AND attribute.attname = 'created_at'
    AND attribute.attnum > 0
    AND NOT attribute.attisdropped;

  IF id_default IS NULL
     OR id_default !~ '^nextval\(.+::regclass\)$'
     OR owned_id_sequence IS NULL
     OR default_id_sequence IS DISTINCT FROM owned_id_sequence
     OR reason_default IS DISTINCT FROM '''''::text'
     OR metadata_default IS DISTINCT FROM '''{}''::text'
     OR created_default IS DISTINCT FROM 'now()'
     OR EXISTS (
       SELECT 1
       FROM pg_attribute AS attribute
       LEFT JOIN pg_attrdef AS default_value
         ON default_value.adrelid = attribute.attrelid
        AND default_value.adnum = attribute.attnum
       WHERE attribute.attrelid = target_table
         AND attribute.attname IN ('issue_id', 'action', 'staff_id')
         AND attribute.attnum > 0
         AND NOT attribute.attisdropped
         AND default_value.oid IS NOT NULL
     ) THEN
    RAISE EXCEPTION
      'migration 274 incompatible defaults: id=%, owned_id_sequence=%, default_id_sequence=%, reason=%, metadata_json=%, created_at=%',
      id_default,
      owned_id_sequence,
      default_id_sequence,
      reason_default,
      metadata_default,
      created_default;
  END IF;

  SELECT
    count(*),
    count(*) FILTER (WHERE constraint_row.contype = 'p')
  INTO constraint_count, primary_key_count
  FROM pg_constraint AS constraint_row
  WHERE constraint_row.conrelid = target_table;

  SELECT array_agg(attribute.attname::TEXT ORDER BY key_column.ordinality)
    INTO primary_key_columns
  FROM pg_constraint AS constraint_row
  CROSS JOIN LATERAL unnest(constraint_row.conkey)
    WITH ORDINALITY AS key_column(attnum, ordinality)
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = constraint_row.conrelid
   AND attribute.attnum = key_column.attnum
  WHERE constraint_row.conrelid = target_table
    AND constraint_row.contype = 'p';

  SELECT count(*)
    INTO extra_unique_index_count
  FROM pg_index AS index_row
  WHERE index_row.indrelid = target_table
    AND index_row.indisunique
    AND NOT index_row.indisprimary;

  IF constraint_count IS DISTINCT FROM 1
     OR primary_key_count IS DISTINCT FROM 1
     OR extra_unique_index_count IS DISTINCT FROM 0
     OR primary_key_columns IS DISTINCT FROM ARRAY['id']::TEXT[] THEN
    RAISE EXCEPTION
      'migration 274 incompatible constraints: total=%, primary_keys=%, extra_unique_indexes=%, primary_key_columns=%',
      constraint_count,
      primary_key_count,
      extra_unique_index_count,
      primary_key_columns;
  END IF;
END;
$migration_274_columns$;

CREATE INDEX IF NOT EXISTS idx_vkpi_data_quality_actions_issue
  ON vkpi_data_quality_actions(issue_id, created_at DESC);

DO $migration_274_index$
DECLARE
  target_table REGCLASS := to_regclass('vkpi_data_quality_actions');
  index_count INTEGER;
  index_method TEXT;
  index_columns TEXT[];
  index_directions INTEGER[];
  index_is_valid BOOLEAN;
  index_is_ready BOOLEAN;
  index_is_unique BOOLEAN;
  index_is_partial BOOLEAN;
  index_has_expressions BOOLEAN;
  index_key_count INTEGER;
  index_attribute_count INTEGER;
BEGIN
  SELECT
    count(*),
    min(access_method.amname),
    bool_and(index_row.indisvalid),
    bool_and(index_row.indisready),
    bool_and(index_row.indisunique),
    bool_or(index_row.indpred IS NOT NULL),
    bool_or(index_row.indexprs IS NOT NULL),
    min(index_row.indnkeyatts),
    min(index_row.indnatts)
  INTO
    index_count,
    index_method,
    index_is_valid,
    index_is_ready,
    index_is_unique,
    index_is_partial,
    index_has_expressions,
    index_key_count,
    index_attribute_count
  FROM pg_class AS index_relation
  JOIN pg_index AS index_row
    ON index_row.indexrelid = index_relation.oid
  JOIN pg_am AS access_method
    ON access_method.oid = index_relation.relam
  WHERE index_relation.relnamespace = (
          SELECT table_relation.relnamespace
          FROM pg_class AS table_relation
          WHERE table_relation.oid = target_table
        )
    AND index_relation.relname = 'idx_vkpi_data_quality_actions_issue'
    AND index_row.indrelid = target_table;

  SELECT
    array_agg(attribute.attname::TEXT ORDER BY key_column.ordinality),
    array_agg((key_option.option_bits & 1)::INTEGER ORDER BY key_option.ordinality)
  INTO index_columns, index_directions
  FROM pg_class AS index_relation
  JOIN pg_index AS index_row
    ON index_row.indexrelid = index_relation.oid
  CROSS JOIN LATERAL unnest(index_row.indkey)
    WITH ORDINALITY AS key_column(attnum, ordinality)
  CROSS JOIN LATERAL unnest(index_row.indoption)
    WITH ORDINALITY AS key_option(option_bits, ordinality)
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = index_row.indrelid
   AND attribute.attnum = key_column.attnum
  WHERE index_relation.relnamespace = (
          SELECT table_relation.relnamespace
          FROM pg_class AS table_relation
          WHERE table_relation.oid = target_table
        )
    AND index_relation.relname = 'idx_vkpi_data_quality_actions_issue'
    AND index_row.indrelid = target_table
    AND key_option.ordinality = key_column.ordinality;

  IF index_count IS DISTINCT FROM 1
     OR index_method IS DISTINCT FROM 'btree'
     OR index_is_valid IS DISTINCT FROM TRUE
     OR index_is_ready IS DISTINCT FROM TRUE
     OR index_is_unique IS DISTINCT FROM FALSE
     OR index_is_partial IS DISTINCT FROM FALSE
     OR index_has_expressions IS DISTINCT FROM FALSE
     OR index_key_count IS DISTINCT FROM 2
     OR index_attribute_count IS DISTINCT FROM 2
     OR index_columns IS DISTINCT FROM ARRAY['issue_id', 'created_at']::TEXT[]
     OR index_directions IS DISTINCT FROM ARRAY[0, 1]::INTEGER[] THEN
    RAISE EXCEPTION
      'migration 274 incompatible index: count=%, method=%, valid=%, ready=%, unique=%, partial=%, expressions=%, keys=%, attrs=%, columns=%, directions=%',
      index_count,
      index_method,
      index_is_valid,
      index_is_ready,
      index_is_unique,
      index_is_partial,
      index_has_expressions,
      index_key_count,
      index_attribute_count,
      index_columns,
      index_directions;
  END IF;
END;
$migration_274_index$;
