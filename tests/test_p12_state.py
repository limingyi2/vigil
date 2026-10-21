import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'brain'))
import agent_runs as runs

def test_resume_inherits_parent_conversation_even_if_http_projection_omits_it(tmp_path):
    with patch.object(runs.paths,'DATA_DIR',str(tmp_path)):
        parent=runs.create({'request_id':'p12_parent','intent':'read','src':'pc','conversation_id':'conv'}, {}, 'owner',start=False)
        with runs.LOCK,runs._db() as db:
            item=runs._get(db,parent['run_id'],'owner')
            runs._finish(db,item,'paused','paused_by_user')
        child=runs.resume(parent['run_id'],'owner',{}, {'request_id':'p12_child'},start=False)
        assert child['conversation_id']=='conv'
        assert child['request_id']=='p12_child'
        assert runs.active('owner','conv')['run_id']==child['run_id']
        with pytest.raises(ValueError,match='会话'):
            runs.resume(parent['run_id'],'owner',{}, {'request_id':'p12_other','conversation_id':'other'},start=False)

def test_tool_detail_redaction_includes_command_assignments_and_environment(tmp_path):
    with patch.dict('os.environ',{'EXAMPLE_API_KEY':'private-value'}):
        result=runs._safe_event_value({'cmd':'API_KEY=hidden echo private-value','token':'credential','output':'Bearer abc123'})
    assert 'private-value' not in str(result)
    assert 'hidden echo' not in str(result)
    assert 'credential' not in str(result)
    assert 'abc123' not in str(result)
    assert 'quoted-secret' not in runs._safe_event_value('API_KEY="quoted-secret" command')
