begin;

alter table public.vocab
add column if not exists deck text;

update public.vocab
set deck = 'hsk1_20'
where level = 'hsk1';

commit;
