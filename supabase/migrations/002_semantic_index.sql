-- Derived index only. SQLite remains authoritative for originals and approval.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create table if not exists public.campus_semantic_chunks (
  chunk_key text primary key,
  document_id uuid not null,
  revision text not null,
  page_number integer not null check (page_number > 0),
  content text not null,
  embedding extensions.vector(768) not null,
  embedding_model text not null,
  embedding_dimensions integer not null check (embedding_dimensions = 768),
  indexed_at timestamptz not null default now()
);
create index if not exists campus_semantic_document on public.campus_semantic_chunks(document_id);
create index if not exists campus_semantic_hnsw on public.campus_semantic_chunks
  using hnsw (embedding extensions.vector_cosine_ops);
alter table public.campus_semantic_chunks enable row level security;
revoke all on public.campus_semantic_chunks from public;
-- No anonymous/student policies: only the backend database role accesses this.
-- This migration can run directly on PostgreSQL or in the Supabase SQL editor.
