-- 0008 memory instructions: per-project guidance for extraction, and up to five custom categories.
-- memory_instructions: free text a project's managers write ("Never keep customer names"; "Also keep
-- on-call runbook steps"). It is added to the knowledge prompt as an adjustment of what counts as
-- worth keeping; it cannot switch off the three tests or the never-memory list (prompts.py).
ALTER TABLE projects ADD COLUMN memory_instructions text
    CHECK (memory_instructions IS NULL OR char_length(memory_instructions) <= 1500);

-- Custom categories: the model may file a kept item under one instead of a built-in. At most five
-- per project (enforced by main.py); slugs never collide with the built-ins.
CREATE TABLE project_categories (
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug         text NOT NULL CHECK (slug ~ '^[a-z0-9-]{2,24}$'
                                      AND slug NOT IN ('decision', 'rule', 'gotcha', 'fact', 'preference')),
    name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
    description  text NOT NULL DEFAULT '' CHECK (char_length(description) <= 300),
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, slug)
);

-- memory_items.category held only the five built-ins; it now also holds a project's custom slugs.
-- A memory keeps its category when that category is later deleted from the project.
DO $$
DECLARE
    c text;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'memory_items'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%category%' LOOP
        EXECUTE format('ALTER TABLE memory_items DROP CONSTRAINT %I', c);
    END LOOP;
END $$;
ALTER TABLE memory_items ADD CONSTRAINT memory_items_category_check CHECK (category ~ '^[a-z0-9-]{2,24}$');
