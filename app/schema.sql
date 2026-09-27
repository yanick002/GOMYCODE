-- Schema Supabase de l'application Portefeuille BRVM.
-- A executer une fois : Supabase > SQL Editor > New query > coller > Run.
--
-- Row Level Security : chaque utilisateur ne voit et ne modifie que ses
-- propres lignes. L'application interroge la base AVEC le jeton de
-- l'utilisateur connecte, jamais avec une cle d'administration : meme un bug
-- cote serveur ne peut pas exposer le portefeuille d'un autre compte.

create table if not exists public.positions (
    id          bigint generated always as identity primary key,
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    ticker      text not null check (ticker ~ '^[A-Z0-9-]{2,10}$'),
    quantite    numeric not null check (quantite > 0),
    prix_achat  numeric not null check (prix_achat >= 0),   -- prix de revient unitaire, en FCFA
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (user_id, ticker)
);

create table if not exists public.liquidites (
    user_id     uuid primary key default auth.uid() references auth.users (id) on delete cascade,
    montant     numeric not null default 0 check (montant >= 0),   -- en FCFA
    updated_at  timestamptz not null default now()
);

alter table public.positions  enable row level security;
alter table public.liquidites enable row level security;

drop policy if exists "positions : lecture"       on public.positions;
drop policy if exists "positions : ajout"         on public.positions;
drop policy if exists "positions : modification"  on public.positions;
drop policy if exists "positions : suppression"   on public.positions;
create policy "positions : lecture"      on public.positions for select using (auth.uid() = user_id);
create policy "positions : ajout"        on public.positions for insert with check (auth.uid() = user_id);
create policy "positions : modification" on public.positions for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "positions : suppression"  on public.positions for delete using (auth.uid() = user_id);

drop policy if exists "liquidites : lecture"      on public.liquidites;
drop policy if exists "liquidites : ajout"        on public.liquidites;
drop policy if exists "liquidites : modification" on public.liquidites;
create policy "liquidites : lecture"      on public.liquidites for select using (auth.uid() = user_id);
create policy "liquidites : ajout"        on public.liquidites for insert with check (auth.uid() = user_id);
create policy "liquidites : modification" on public.liquidites for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
