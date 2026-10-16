-- Run only in the intended new FootyLevels Free project.
-- Private buckets; no anonymous/authenticated policies are granted here.
-- Future API code authorizes users and creates scoped, temporary asset capabilities.
begin;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('private-media', 'private-media', false, 52428800,
   array['video/mp4', 'video/quicktime', 'image/jpeg', 'image/png',
         'image/heic', 'image/heif', 'image/webp',
         'application/json', 'application/octet-stream', 'application/gzip',
         'model/vnd.usdz+zip', 'model/gltf-binary']),
  ('reference-assets', 'reference-assets', false, 52428800,
   array['video/mp4', 'video/quicktime', 'image/jpeg', 'image/png',
         'image/heic', 'image/heif', 'image/webp', 'application/json',
         'application/octet-stream', 'application/gzip',
         'model/vnd.usdz+zip', 'model/gltf-binary'])
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

commit;

select id, public, file_size_limit, allowed_mime_types
from storage.buckets
where id in ('private-media', 'reference-assets');
