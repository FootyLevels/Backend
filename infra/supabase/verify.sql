-- Read-only evidence for the intended new FootyLevels project.
select id, public, file_size_limit, allowed_mime_types
from storage.buckets
where id in ('private-media', 'reference-assets');

-- New projects should have no client Storage policies. Review any returned row;
-- permissive policies combine with OR and may expose objects across buckets.
select policyname, roles, cmd, qual, with_check
from pg_policies
where schemaname = 'storage' and tablename = 'objects';

select
  (select count(*) from pg_policies
   where schemaname = 'storage' and tablename = 'objects') as client_policy_count,
  relrowsecurity as object_rls_enabled
from pg_class
where oid = 'storage.objects'::regclass;
