-- Supabase Auth owns identities. Only the backend/operator may assign app roles.
CREATE TABLE IF NOT EXISTS campus.staff_members (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('admin', 'staff')),
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid REFERENCES auth.users(id) ON DELETE SET NULL
);
ALTER TABLE campus.staff_members ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON campus.staff_members FROM PUBLIC, anon, authenticated;
