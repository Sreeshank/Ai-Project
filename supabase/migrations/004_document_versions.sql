create table if not exists campus.document_replacements (
 replacement_id text primary key references campus.documents(id),
 previous_id text not null unique references campus.documents(id),
 check(replacement_id != previous_id));
alter table campus.document_replacements enable row level security;
revoke all on campus.document_replacements from public;
