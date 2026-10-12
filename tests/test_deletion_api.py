from fastapi.testclient import TestClient
from api.cloud import create_app
from footylevels.records import DeletionRecord
from footylevels.repositories import RecordNotFound
from tests.test_cloud_api import TestRecords, Verifier, headers


class Deletions:
    def __init__(self, records): self.records, self.items=records,{}
    def begin_project(self, uid, project):
        self.records.mark_deleting(uid,project)
        record=DeletionRecord(id='deletion-'+project,owner_id=uid,scope='project',project_id=project)
        self.items[record.id]=record
        return record
    def progress(self,uid,identifier):
        item=self.items.get(identifier)
        if not item or item.owner_id!=uid:raise RecordNotFound()
        return item
    def list_progress(self,uid):return [i for i in self.items.values() if i.owner_id==uid]
    def begin_account(self,uid):
        record=DeletionRecord(id='account-'+uid,owner_id=uid,scope='account')
        self.records.users[uid]=self.records.users[uid].model_copy(update={'account_state':'deleting'})
        self.items[record.id]=record
        return record


def test_deletion_routes_are_owned_and_truthful_after_account_restriction():
    records=TestRecords();deletions=Deletions(records)
    client=TestClient(create_app(records,Verifier(),deletions=deletions))
    for user in ['alice','bob']:client.post('/api/me/bootstrap',headers=headers(user))
    p=client.post('/api/projects',headers=headers('alice'),json={'title':'Clip','source':'gallery'}).json()['id']
    assert client.delete('/api/projects/'+p,headers=headers('bob')).status_code==404
    reply=client.delete('/api/projects/'+p,headers=headers('alice'))
    assert reply.status_code==202 and reply.json()['state']=='deleting'
    identifier=reply.json()['deletionId']
    assert client.get('/api/projects/'+p,headers=headers('alice')).status_code==404
    assert client.get('/api/deletions/'+identifier).status_code==401
    assert client.get('/api/deletions/'+identifier,headers=headers('bob')).status_code==404
    progress=client.get('/api/deletions/'+identifier,headers=headers('alice')).json()
    assert progress['state']=='pending' and 'asset' not in progress
    assert client.get('/api/deletions',headers=headers('bob')).json()['items']==[]
    account=client.delete('/api/me/data',headers=headers('alice'))
    assert account.status_code==202 and account.json()['scope']=='account'
    assert client.get('/api/projects',headers=headers('alice')).status_code==403
    assert client.get('/api/deletions/'+account.json()['id'],headers=headers('alice')).status_code==200
