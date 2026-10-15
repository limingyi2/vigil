import json
import unittest
from tests import test_codex_adapter as fixtures


class TimelineMetadataTests(unittest.TestCase):
    setUp = fixtures.AdapterTests.setUp
    tearDown = fixtures.AdapterTests.tearDown
    event = fixtures.AdapterTests.event
    send = fixtures.AdapterTests.send
    start = fixtures.AdapterTests.start
    approval = fixtures.AdapterTests.approval

    def test_state_transitions_preserve_existing_approval_lifecycle(self):
        self.start()
        self.send(self.event('thread/status/changed', status={'type':'active','activeFlags':['waitingOnApproval']}))
        self.send(self.approval())
        self.assertEqual(len(self.center.approvals()),1)
        self.send(self.event('thread/status/changed', status={'type':'active','activeFlags':[]}))
        self.send(self.event('turn/completed', turn={'id':'turn-1','status':'completed'}))
        transitions=[(e['previous_state'],e['state']) for e in self.center.event_log()['items'] if e.get('state_transition')]
        self.assertEqual(transitions,[('idle','running'),('running','waiting_approval'),('waiting_approval','running'),('running','completed')])
        self.assertEqual(self.center.approvals()[0]['state'],'expired')

    def test_tool_item_identity_and_duration_only_expose_metadata(self):
        self.start()
        for method,status in [('item/started','inProgress'),('item/completed','completed')]:
            self.send(self.event(method,turnId='turn-1',item={'id':'tool-a','type':'commandExecution',
                'status':status,'exitCode':0,'durationMs':42,'command':'PRIVATE','arguments':{'key':'PRIVATE'},'aggregatedOutput':'PRIVATE'}))
        events=[e for e in self.center.event_log()['items'] if e['method'].startswith('item/')]
        self.assertEqual(events[0]['item_key'],events[1]['item_key'])
        self.assertEqual(events[1]['duration_ms'],42)
        self.assertFalse(events[0]['state_transition'])
        self.assertNotIn('PRIVATE',json.dumps(events))

    def test_disconnect_records_unknown_transition_without_live_claim(self):
        self.start()
        self.center._disconnect_generation(self.center.generation,'transport_closed')
        event=next(e for e in self.center.event_log()['items'] if e['method']=='task/status/changed')
        self.assertEqual((event['previous_state'],event['state']),('running','unknown'))
        self.assertEqual(event['turn'],self.center._label('turn-1'))
        self.assertFalse(self.center.get_task(self.task_id)['live'])

    def test_late_completion_is_not_a_new_task_transition(self):
        self.start()
        self.start('turn-2')
        self.send(self.event('turn/completed',turn={'id':'turn-1','status':'completed'}))
        event=self.center.event_log()['items'][-1]
        self.assertEqual(event['method'],'turn/late_completion')
        self.assertNotIn('state_transition',event)
        self.assertEqual(self.center.get_task(self.task_id)['status'],'running')
