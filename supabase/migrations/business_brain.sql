-- Business Brain: per-business context the AI writer uses to sound on-brand.
create table if not exists public.business_brain (
  id            text primary key,           -- e.g. 'demo', or a client slug
  business_name text not null,
  industry      text,
  services      text,
  offers        text,
  audience      text,
  brand_voice   text default 'warm, confident, and helpful',
  created_at    timestamptz default now()
);

-- The Edge Function reads this with the service role key (bypasses RLS).
-- Enable RLS so the public anon key cannot read/write client business data.
alter table public.business_brain enable row level security;

-- Seed the demo business so the live demo shows real personalization.
insert into public.business_brain (id, business_name, industry, services, offers, audience, brand_voice)
values (
  'demo',
  'Luminara Med Spa',
  'luxury medical spa',
  'Botox, HydraFacial, laser skin resurfacing, dermal fillers, microneedling',
  'New client special: $100 off your first treatment package',
  'women 30-55 who value premium skincare and self-care',
  'elegant, warm, and confident; aspirational but never pushy'
)
on conflict (id) do update set
  business_name = excluded.business_name,
  industry      = excluded.industry,
  services      = excluded.services,
  offers        = excluded.offers,
  audience      = excluded.audience,
  brand_voice   = excluded.brand_voice;
