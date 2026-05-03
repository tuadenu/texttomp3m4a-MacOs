begin;

alter table public.vocab
add column if not exists version text;

update public.vocab
set version = '2.0'
where deck like '%_20';

commit;
