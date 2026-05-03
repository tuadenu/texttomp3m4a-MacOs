create table if not exists public.vocab (
  id bigserial primary key,
  word text not null,
  meaning text,
  audio text,
  audio_url text,
  level text not null,
  "index" integer not null,
  example text,
  example_vi text,
  created_at timestamptz not null default now()
);

create unique index if not exists vocab_level_index_key
  on public.vocab (level, "index");

create index if not exists vocab_word_idx
  on public.vocab (word);

alter table public.vocab enable row level security;

-- Allow app clients to read vocab publicly.
do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'vocab'
      and policyname = 'Public read vocab'
  ) then
    create policy "Public read vocab"
      on public.vocab
      for select
      to anon, authenticated
      using (true);
  end if;
end
$$;
