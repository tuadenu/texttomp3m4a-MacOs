-- Thay gia tri o day truoc khi chay:
-- target_deck: vi du 'hsk3_30'
-- target_level: vi du 'hsk3'

update public.vocab
set deck = 'target_deck'
where level = 'target_level';
