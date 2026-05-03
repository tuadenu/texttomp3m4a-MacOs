-- Thay gia tri o day truoc khi chay:
-- target_version: vi du '3.0'
-- deck_suffix: vi du '_30'

update public.vocab
set version = 'target_version'
where deck like '%' || 'deck_suffix';
