"""Migration 004: Performance indexes for common query patterns."""
VERSION = 4
DESCRIPTION = "Indexes on tasks, agents, messages, idle_runs"


def up(conn):
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_messages_inbox
        ON messages (to_agent, channel, read_at);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to, status);
    CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_depends ON tasks(depends_on) WHERE depends_on IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
    CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role);
    CREATE INDEX IF NOT EXISTS idx_idle_runs_agent ON idle_runs(agent);
    CREATE INDEX IF NOT EXISTS idx_idle_runs_created ON idle_runs(created_at);
    CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent);
    """)


def down(conn):
    pass
