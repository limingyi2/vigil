"""Additive P18 schema. Only called inside the main DB migration transaction."""


def migrate(conn):
    statements = [
        """CREATE TABLE task_workflows (
            workflow_id TEXT PRIMARY KEY, owner TEXT NOT NULL, src TEXT NOT NULL,
            persona TEXT NOT NULL, conversation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL, workspace_key TEXT NOT NULL,
            goal TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
                ('ready','active','paused','failed','cancelled','needs_review','completed')),
            plan_version INTEGER NOT NULL CHECK(plan_version>0),
            revision INTEGER NOT NULL CHECK(revision>0),
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE INDEX ix_task_scope ON task_workflows
            (owner,src,persona,conversation_id,workspace_id)""",
        """CREATE TABLE task_plans (
            workflow_id TEXT NOT NULL REFERENCES task_workflows(workflow_id),
            plan_version INTEGER NOT NULL, definition TEXT NOT NULL,
            created_at REAL NOT NULL, PRIMARY KEY(workflow_id,plan_version))""",
        """CREATE TABLE task_steps (
            workflow_id TEXT NOT NULL, plan_version INTEGER NOT NULL,
            step_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
                ('pending','running','paused','failed','cancelled','needs_review','completed')),
            evidence TEXT NOT NULL,
            PRIMARY KEY(workflow_id,plan_version,step_id),
            FOREIGN KEY(workflow_id,plan_version)
                REFERENCES task_plans(workflow_id,plan_version))""",
        """CREATE TABLE task_run_links (
            workflow_id TEXT NOT NULL REFERENCES task_workflows(workflow_id),
            run_id TEXT NOT NULL REFERENCES runs(id), owner TEXT NOT NULL,
            linked_at REAL NOT NULL, PRIMARY KEY(workflow_id,run_id), UNIQUE(run_id))""",
        """CREATE TABLE task_attempts (
            attempt_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL, step_id TEXT NOT NULL,
            run_id TEXT NOT NULL, tool TEXT NOT NULL, effect TEXT NOT NULL
                CHECK(effect IN ('read','write','delete','command','other')),
            dispatch_key TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                ('prepared','dispatched','returned','failed','denied','not_dispatched','unknown')),
            result_ref TEXT, evidence TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(workflow_id,dispatch_key),
            FOREIGN KEY(workflow_id,plan_version,step_id)
                REFERENCES task_steps(workflow_id,plan_version,step_id),
            FOREIGN KEY(workflow_id,run_id) REFERENCES task_run_links(workflow_id,run_id))""",
        """CREATE TABLE task_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES task_workflows(workflow_id),
            revision INTEGER NOT NULL, reason TEXT NOT NULL,
            snapshot TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(workflow_id,revision))""",
        """CREATE TABLE task_requests (
            owner TEXT NOT NULL, src TEXT NOT NULL, persona TEXT NOT NULL,
            conversation_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            checkpoint_id TEXT NOT NULL REFERENCES task_checkpoints(checkpoint_id),
            PRIMARY KEY(owner,src,persona,conversation_id,workspace_id,request_id))""",
    ]
    for statement in statements:
        conn.execute(statement)
