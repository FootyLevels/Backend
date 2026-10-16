"""Owner-only deletion progress and bounded, resumable private-object cleanup.

No prefix deletion, capability bypass or cloud queue. Minimal tombstones remain
so idempotency replay cannot recreate removed recordings or accounts.
"""
from datetime import timedelta
import hashlib
import json

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .assets import StorageUnavailable
from .records import AssetRef, DeletionRecord, ProjectRecord, UserRecord, utc_now
from .repositories import RecordConflict, RecordNotFound
from .media_jobs import FirestoreMediaJobs


def deletion_id(uid, project_id=None):
    return hashlib.sha256(json.dumps([uid, project_id, 'delete-v1']).encode()).hexdigest()


class FirestoreDeletions(FirestoreMediaJobs):
    BATCH = 20

    def progress(self, uid, identifier):
        record = self._record(self._ref('deletions', identifier).get(), DeletionRecord)
        if record.owner_id != uid:
            raise RecordNotFound()
        return record

    def list_progress(self, uid):
        query = self.client.collection('deletions').where(filter=FieldFilter('ownerId', '==', uid)).where(
            filter=FieldFilter('state', '==', 'pending'))
        return [self._record(s, DeletionRecord) for s in query.limit(50).stream()]

    def begin_project(self, uid, project_id):
        self.mark_deleting(uid, project_id)
        return self.progress(uid, deletion_id(uid, project_id))

    def begin_account(self, uid):
        ref = self._ref('users', uid)
        intent = self._ref('deletions', deletion_id(uid))
        @firestore.transactional
        def begin(transaction):
            user = self._record(ref.get(transaction=transaction), UserRecord)
            existing = intent.get(transaction=transaction)
            if existing.exists:
                return self._record(existing, DeletionRecord)
            if user.account_state not in {'active', 'restricted'}:
                raise RecordConflict('Account cleanup requires reconciliation')
            now = utc_now()
            record = DeletionRecord(id=intent.id, owner_id=uid, scope='account',
                                    not_before=now + timedelta(minutes=5))
            transaction.update(ref, {'accountState': 'deleting', 'updatedAt': now})
            transaction.create(intent, record.document())
            return record
        return self._run(begin)

    def _project_records(self, collection, project):
        snapshots = list(self.client.collection(collection).where(
            filter=FieldFilter('projectId', '==', project.id)).limit(self.BATCH).stream())
        if any(s.to_dict().get('ownerId') != project.owner_id for s in snapshots):
            raise RecordConflict('Cleanup ownership requires reconciliation')
        return snapshots

    def _descendants(self, ref, budget, depth=0):
        """Explicitly remove scoped subcollections; retaining a root is not enough."""
        if depth > 16:
            raise RecordConflict('Unexpected metadata nesting')
        for collection in ref.collections():
            for snapshot in collection.limit(max(1, budget[0])).stream():
                if budget[0] <= 0:
                    return False
                if not self._descendants(snapshot.reference, budget, depth + 1):
                    return False
                snapshot.reference.delete()
                budget[0] -= 1
            if budget[0] <= 0:
                return False
        return True

    def _remove_metadata(self, snapshot, budget):
        if not self._descendants(snapshot.reference, budget):
            return False
        snapshot.reference.delete()
        budget[0] -= 1
        return budget[0] > 0

    def _asset(self, project, raw, collection):
        asset = AssetRef.model_validate(raw['asset'])
        prefix = f'users/{project.owner_id}/projects/{project.id}/'
        if (asset.bucket != 'private-media' or not asset.object_key.startswith(prefix)
                or raw.get('ownerId') != project.owner_id or raw.get('projectId') != project.id):
            raise RecordConflict('Asset provenance requires reconciliation')
        tail = asset.object_key[len(prefix):].split('/')
        if collection == 'uploads':
            valid = len(tail) == 2 and tail[0] == 'source'
        else:
            valid = len(tail) == 4 and tail[0] in {'media', 'reconstruction'}
        if not valid:
            raise RecordConflict('Unsupported asset path')
        return asset

    def _remove_asset(self, project, snapshot, storage, collection):
        raw = snapshot.to_dict()
        asset = self._asset(project, raw, collection)
        after = max(raw['cleanupAfter'], raw['expiresAt'] + timedelta(hours=48)) if collection == 'uploads' else raw['createdAt'] + timedelta(hours=24)
        if after > utc_now():
            return after
        storage.delete_exact(asset)
        storage.verify_absent(asset)
        if not self._descendants(snapshot.reference, [self.BATCH]):
            return utc_now()
        field = 'reservedBytes' if collection == 'uploads' else 'bytes'
        ledgers = self._ledgers(project.owner_id)
        @firestore.transactional
        def release(transaction):
            current = snapshot.reference.get(transaction=transaction)
            root = self._record(self._ref('projects', project.id).get(transaction=transaction), ProjectRecord)
            counts = [(ref.get(transaction=transaction).to_dict() or {}).get('bytes', 0) for ref in ledgers]
            if root.owner_id != project.owner_id or root.state != 'deleting':
                raise RecordConflict('Deletion fence changed')
            if not current.exists:
                return
            body = current.to_dict()
            if body.get('asset') != raw['asset'] or body.get('ownerId') != project.owner_id:
                raise RecordConflict('Reservation changed')
            amount = body.get(field, 0)
            if not isinstance(amount, int) or amount < 0 or any(n < amount for n in counts):
                raise RecordConflict('Storage accounting requires reconciliation')
            now = utc_now()
            for ref, count in zip(ledgers, counts):
                transaction.set(ref, {'bytes': count - amount, 'updatedAt': now})
            transaction.delete(snapshot.reference)
        self._run(release)
        return None

    def _update(self, record, **changes):
        ref = self._ref('deletions', record.id)
        @firestore.transactional
        def update(transaction):
            current = self._record(ref.get(transaction=transaction), DeletionRecord)
            if current.state == 'completed':
                return current
            result = current.model_copy(update={**changes, 'updated_at': utc_now()})
            transaction.set(ref, result.document())
            return result
        return self._run(update)

    def _project(self, record, storage):
        project_ref = self._ref('projects', record.project_id)
        project = self._record(project_ref.get(), ProjectRecord)
        if project.owner_id != record.owner_id or project.state not in {'deleting', 'deleted'}:
            raise RecordConflict('Project tombstone is required')
        if project.state == 'deleted':
            return self._update(record, state='completed', completed_at=utc_now(), last_error=None)
        waiting = []
        remaining = False
        for collection in ['uploads', 'assetReservations']:
            snapshots = self._project_records(collection, project)
            remaining |= bool(snapshots)
            for snapshot in snapshots:
                until = self._remove_asset(project, snapshot, storage, collection)
                if until:
                    waiting.append(until)
        if remaining:
            return self._update(record, not_before=max(waiting) if waiting else utc_now(), last_error=None)
        # Every registered object is gone. Fence physical cleanup against missing
        # accounting records too, before removing the last published references.
        published = [project.video] if project.video else []
        if project.prepared_media:
            published += [project.prepared_media.video, *[p.asset for p in project.prepared_media.previews]]
        for asset in published:
            storage.verify_absent(asset)
        budget = [self.BATCH]
        for snapshot in self._project_records('reconstructions', project):
            raw = snapshot.to_dict()
            for asset in raw.get('artifacts', []):
                storage.verify_absent(AssetRef.model_validate(asset))
            for field in ['leftReconstructionId', 'rightId']:
                query = self.client.collection('comparisons').where(filter=FieldFilter(field, '==', snapshot.id))
                for comparison in query.limit(max(1, budget[0])).stream():
                    if field == 'rightId' and comparison.to_dict().get('rightType') != 'reconstruction':
                        continue
                    if comparison.to_dict().get('ownerId') != record.owner_id:
                        raise RecordConflict('Comparison owner mismatch')
                    if not self._remove_metadata(comparison, budget):
                        return record
            if not self._remove_metadata(snapshot, budget):
                return record
        if self._project_records('reconstructions', project):
            return record
        for snapshot in self._project_records('jobs', project):
            if not self._remove_metadata(snapshot, budget):
                return record
        if self._project_records('jobs', project) or not self._descendants(project_ref, budget):
            return record
        @firestore.transactional
        def complete(transaction):
            current = self._record(project_ref.get(transaction=transaction), ProjectRecord)
            intent = self._record(self._ref('deletions', record.id).get(transaction=transaction), DeletionRecord)
            if intent.state == 'completed':
                return intent
            if current.owner_id != record.owner_id or current.state not in {'deleting', 'deleted'}:
                raise RecordConflict('Project deletion fence changed')
            now = utc_now()
            # The retained ID alone fences an old create request. Erase its
            # title-derived creation hash and original capture/gallery metadata.
            erased = ProjectRecord(id=current.id, owner_id=current.owner_id, title='Deleted recording',
                source='gallery', state='deleted',
                input_revision=current.input_revision, deleted_at=current.deleted_at,
                created_at=current.created_at, updated_at=now)
            done = intent.model_copy(update={'state':'completed', 'completed_at':now,
                                            'updated_at':now, 'last_error':None})
            transaction.set(project_ref, erased.document())
            transaction.set(self._ref('deletions', record.id), done.document())
            return done
        return self._run(complete)

    def _account(self, record):
        # Account profile disables ordinary API access immediately. Project
        # creation rereads that profile transactionally, so this scan cannot miss
        # a recording created after the account tombstone.
        snapshots = list(self.client.collection('projects').where(
            filter=FieldFilter('ownerId', '==', record.owner_id)).where(
            filter=FieldFilter('state', 'in', ['uploading','preparing','ready','rejected','deleting'])).limit(self.BATCH).stream())
        if snapshots:
            for snapshot in snapshots:
                self.begin_project(record.owner_id, snapshot.id)
            return record
        budget = [self.BATCH]
        comparisons = self.client.collection('comparisons').where(filter=FieldFilter('ownerId', '==', record.owner_id))
        for snapshot in comparisons.limit(self.BATCH).stream():
            if not self._remove_metadata(snapshot, budget):
                return record
        for collection in ['uploads', 'assetReservations', 'jobs', 'reconstructions']:
            if list(self.client.collection(collection).where(filter=FieldFilter('ownerId', '==', record.owner_id)).limit(1).stream()):
                raise RecordConflict('Unscoped account data requires reconciliation')
        ref = self._ref('users', record.owner_id)
        if not self._descendants(ref, [self.BATCH]):
            return record
        @firestore.transactional
        def finish(transaction):
            user = self._record(ref.get(transaction=transaction), UserRecord)
            intent = self._record(self._ref('deletions', record.id).get(transaction=transaction), DeletionRecord)
            if user.account_state not in {'deleting','deleted'}:
                raise RecordConflict('Account deletion fence changed')
            now = utc_now()
            transaction.set(ref, UserRecord(id=user.id, display_name='Deleted account', account_state='deleted',
                created_at=user.created_at, updated_at=now).document())
            done = intent.model_copy(update={'state':'completed','completed_at':now,'updated_at':now,'last_error':None})
            transaction.set(self._ref('deletions', record.id), done.document())
            return done
        return self._run(finish)

    def process(self, identifier, storage):
        record = self._record(self._ref('deletions', identifier).get(), DeletionRecord)
        if record.state == 'completed' or record.not_before > utc_now():
            return record
        try:
            return self._project(record, storage) if record.scope == 'project' else self._account(record)
        except StorageUnavailable:
            return self._update(record, last_error='cleanup_unavailable')
        except (RecordConflict, RecordNotFound, ValueError):
            return self._update(record, last_error='reconciliation_required')

    def reconcile_deletions(self, storage, cursors):
        query = self.client.collection('deletions').where(filter=FieldFilter('state', '==', 'pending')).order_by('__name__')
        if cursors.get('deletions'):
            query = query.start_after({'__name__':self._ref('deletions',cursors['deletions'])})
        snapshots = list(query.limit(10).stream())
        cursors['deletions'] = snapshots[-1].id if snapshots else None
        completed = 0
        for snapshot in snapshots:
            completed += self.process(snapshot.id, storage).state == 'completed'
        return completed
