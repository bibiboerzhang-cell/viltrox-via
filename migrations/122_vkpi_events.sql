-- 122: V-KPI Events module - Core event management tables for activities, tasks, expenses, KOL invites.
-- Supports multi-team collaboration, budget tracking, health scoring, and location data.
-- All ForeignKey constraints use ON DELETE CASCADE for data integrity.

CREATE TABLE IF NOT EXISTS vkpi_events (
    id VARCHAR(64) PRIMARY KEY,
    title TEXT NOT NULL,
    type_key TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'planning',
    health_score INTEGER DEFAULT 100,
    note TEXT DEFAULT '',
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    location_name TEXT DEFAULT '',
    location_city TEXT DEFAULT '',
    location_country TEXT DEFAULT '',
    location_lat NUMERIC(10,6) DEFAULT NULL,
    location_lng NUMERIC(10,6) DEFAULT NULL,
    budget_total INTEGER DEFAULT 0,
    budget_json JSONB DEFAULT '{}',
    owner_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    team_ids JSONB DEFAULT '[]',
    related_project_ids JSONB DEFAULT '[]',
    invited_kols_json JSONB DEFAULT '[]',
    roi NUMERIC(10,2) DEFAULT NULL,
    leads INTEGER DEFAULT NULL,
    videos INTEGER DEFAULT NULL,
    retrospective TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_events_status ON vkpi_events(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_events_owner ON vkpi_events(owner_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_events_start_date ON vkpi_events(start_date DESC);

CREATE TABLE IF NOT EXISTS vkpi_event_tasks (
    id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    phase TEXT DEFAULT 'prep',
    owner TEXT DEFAULT '',
    collaborators JSONB DEFAULT '[]',
    due_date DATE DEFAULT NULL,
    kind TEXT DEFAULT 'task',
    checklist JSONB DEFAULT '[]',
    details JSONB DEFAULT '{}',
    done BOOLEAN DEFAULT FALSE,
    done_at TIMESTAMPTZ DEFAULT NULL,
    done_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_tasks_event ON vkpi_event_tasks(event_id, due_date ASC);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_tasks_owner ON vkpi_event_tasks(owner);

CREATE TABLE IF NOT EXISTS vkpi_event_expenses (
    id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL DEFAULT 0,
    category TEXT DEFAULT 'other',
    description TEXT DEFAULT '',
    paid_by TEXT DEFAULT '',
    payment_method TEXT DEFAULT 'other',
    reimbursement_status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_expenses_event ON vkpi_event_expenses(event_id, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_event_kol_invites (
    id VARCHAR(64) PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL REFERENCES vkpi_events(id) ON DELETE CASCADE,
    kol_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    days TEXT DEFAULT '',
    travel_status TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_event_kol_invites_event ON vkpi_event_kol_invites(event_id);

COMMENT ON TABLE vkpi_events IS 'V-KPI Events - Main event records for tradeshow/meetup/media events with budget and team info';
COMMENT ON TABLE vkpi_event_tasks IS 'Event-scoped tasks with phase, ownership, and completion tracking';
COMMENT ON TABLE vkpi_event_expenses IS 'Event-scoped expense ledger with reimbursement status';
COMMENT ON TABLE vkpi_event_kol_invites IS 'KOL invitation tracking for each event';
