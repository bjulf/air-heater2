-- Run as administrator in pgAdmin, connected to airheater.
-- Only setpoint is added. Existing measurements and checkpoints are preserved.
BEGIN;
DO $$
BEGIN
    IF current_database() <> 'airheater' THEN
        RAISE EXCEPTION 'Connect to airheater before running this migration';
    END IF;
END $$;
ALTER TABLE public.simulation_measurements
    ADD COLUMN IF NOT EXISTS setpoint_c double precision;
-- Compatibility if the earlier, expanded draft migration was already applied.
-- Its unused columns are retained; only that draft constraint is removed.
ALTER TABLE public.simulation_measurements
    DROP CONSTRAINT IF EXISTS control_telemetry_valid;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conname = 'control_setpoint_valid'
          AND conrelid = 'public.simulation_measurements'::regclass) THEN
        ALTER TABLE public.simulation_measurements
            ADD CONSTRAINT control_setpoint_valid
            CHECK (setpoint_c IS NULL OR setpoint_c BETWEEN -50 AND 150);
    END IF;
END $$;
COMMIT;
