"""P7.2: a workspace is a real local folder; a chat is only a conversation in it."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))
import conversations
import workspaces

OWNER, SRC, PERSONA = 'owner', 'pc', 'default'


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = patch.object(workspaces.paths, 'DATA_DIR', self.tmp.name)
        self.data.start()
        # conversations.py resolves the same module object, so one patch covers both.
        self.folder = os.path.join(self.tmp.name, 'demo project')
        os.makedirs(self.folder, exist_ok=True)

    def tearDown(self):
        self.data.stop()
        self.tmp.cleanup()

    def test_path_must_be_absolute(self):
        with self.assertRaises(ValueError):
            workspaces.create(OWNER, SRC, PERSONA, 'relative/folder')
        with self.assertRaises(ValueError):
            workspaces.create(OWNER, SRC, PERSONA, '')

    def test_same_folder_is_one_workspace(self):
        first = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        second = workspaces.create(OWNER, SRC, PERSONA, self.folder + os.sep + '.')
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['existing'])
        self.assertEqual(len(workspaces.list_for(OWNER, SRC, PERSONA)), 1)
        self.assertEqual(first['name'], 'demo project')
        self.assertEqual(os.path.normpath(first['path']), os.path.normpath(self.folder))

    def test_workspace_holds_many_conversations(self):
        ws = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        one = conversations.create(OWNER, SRC, PERSONA, ws['id'])
        two = conversations.create(OWNER, SRC, PERSONA, ws['id'])
        self.assertNotEqual(one['id'], two['id'])
        in_ws = [c for c in conversations.list_for(OWNER, SRC, PERSONA)
                 if c['workspace_id'] == ws['id']]
        self.assertEqual(len(in_ws), 2)

    def test_switching_never_reassigns(self):
        ws = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        inside = conversations.create(OWNER, SRC, PERSONA, ws['id'])
        loose = conversations.create(OWNER, SRC, PERSONA)
        self.assertIsNone(loose['workspace_id'])
        # Reading back is not a reassignment, in either direction.
        self.assertEqual(conversations.get(OWNER, SRC, PERSONA, inside['id'])['workspace_id'], ws['id'])
        self.assertIsNone(conversations.get(OWNER, SRC, PERSONA, loose['id'])['workspace_id'])

    def test_explicit_assign_and_detach(self):
        ws = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        conv = conversations.create(OWNER, SRC, PERSONA)
        moved = conversations.assign(OWNER, SRC, PERSONA, conv['id'], ws['id'])
        self.assertEqual(moved['workspace_id'], ws['id'])
        back = conversations.assign(OWNER, SRC, PERSONA, conv['id'], None)
        self.assertIsNone(back['workspace_id'])

    def test_removing_workspace_keeps_chats(self):
        ws = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        conv = conversations.create(OWNER, SRC, PERSONA, ws['id'])
        detached = conversations.detach_workspace(OWNER, SRC, PERSONA, ws['id'])
        self.assertEqual(detached, 1)
        workspaces.remove(OWNER, SRC, PERSONA, ws['id'])
        self.assertEqual(workspaces.list_for(OWNER, SRC, PERSONA), [])
        self.assertIsNone(conversations.get(OWNER, SRC, PERSONA, conv['id'])['workspace_id'])
        # The chat itself survives and stays readable.
        self.assertTrue(conversations.touch(OWNER, SRC, PERSONA, conv['id'], 'hello'))

    def test_persisted_across_restart(self):
        ws = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        conv = conversations.create(OWNER, SRC, PERSONA, ws['id'])
        # A restart only drops in-process caches; both rows live in SQLite.
        again = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        self.assertEqual(again['id'], ws['id'])
        self.assertEqual(conversations.get(OWNER, SRC, PERSONA, conv['id'])['workspace_id'], ws['id'])

    def test_old_database_gets_column_without_losing_rows(self):
        # Simulate a brain created before workspaces existed.
        import sqlite3
        path = os.path.join(self.tmp.name, 'conversations.sqlite3')
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE conversations (owner TEXT NOT NULL, src TEXT NOT NULL,'
                   ' persona TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,'
                   ' created REAL NOT NULL, updated REAL NOT NULL,'
                   ' legacy INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,src,persona,id))')
        db.execute("INSERT INTO conversations VALUES ('owner','pc','default','cid-old','旧会话',1,2,0)")
        db.commit()
        db.close()
        rows = conversations.list_for(OWNER, SRC, PERSONA)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], '旧会话')
        self.assertIsNone(rows[0]['workspace_id'])

    def test_isolation_between_personas(self):
        mine = workspaces.create(OWNER, SRC, PERSONA, self.folder)
        other = workspaces.create(OWNER, SRC, 'other', self.folder)
        self.assertNotEqual(mine['id'], other['id'])
        self.assertEqual([item['id'] for item in workspaces.list_for(OWNER, SRC, 'other')], [other['id']])
        self.assertIsNone(workspaces.get(OWNER, SRC, 'other', mine['id']))

    def test_display_name_and_reject_bad_input(self):
        self.assertEqual(workspaces.display_name('C:\\work\\demo'), 'demo')
        self.assertEqual(workspaces.display_name('C:\\'), 'C:\\')
        self.assertEqual(workspaces.display_name('/home/user/demo'), 'demo')
        self.assertEqual(workspaces.display_name('/'), '/')
        self.assertEqual(workspaces.normalize_path('C:\\work\\demo')[0], 'C:\\work\\demo')
        self.assertEqual(workspaces.normalize_path('/home/user/demo')[0], '/home/user/demo')
        self.assertEqual(workspaces.normalize_path('bad\x00path'), ('', ''))
        self.assertEqual(workspaces.normalize_path('x' * 2000), ('', ''))


if __name__ == '__main__':
    unittest.main()
