-- Legacy hosting schema, reserved for moving originals/accounts to Supabase.
-- The current API uses SQLite originals plus the separate 002 semantic index.
-- Keep this private until explicit Auth roles and Storage policies are implemented.
create extension if not exists vector with schema extensions;

create table public.campus_documents (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  department text not null,
  status text not null default 'pending' check (status in ('pending','approved','archived')),
  version integer not null default 1,
  storage_path text not null,
  effective_from date,
  effective_until date,
  created_at timestamptz not null default now()
);

create table public.campus_chunks (
  id bigint generated always as identity primary key,
  document_id uuid not null references public.campus_documents(id) on delete cascade,
  page_number integer not null,
  content text not null,
  fts tsvector generated always as (to_tsvector('english', content)) stored,
  embedding extensions.vector(768),
  embedding_model text,
  unique (document_id, id)
);
create index campus_chunks_fts on public.campus_chunks using gin(fts);
create index campus_chunks_embedding on public.campus_chunks using hnsw(embedding extensions.vector_cosine_ops);
alter table public.campus_documents enable row level security;
alter table public.campus_chunks enable row level security;
-- No public policies: access is denied by default. Add reviewed staff/student
-- policies together with the repository adapter, never a blanket allow-all policy.
