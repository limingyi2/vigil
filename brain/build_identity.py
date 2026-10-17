"""Public build identity only. No configuration or credentials in this response."""
import json
import os
import sys

PROTOCOL = 1
CAPABILITIES = ['creator_jobs', 'agent_runs', 'models_saved', 'chat_stream']


def identity():
    root = getattr(sys, '_MEIPASS', os.path.dirname(__file__))
    try:
        with open(os.path.join(root, 'build-info.json'), encoding='utf-8') as f:
            build = json.load(f)
    except FileNotFoundError:
        build = {'version': 'development', 'commit': '', 'source_digest': ''}
    return dict(build, protocol=PROTOCOL, capabilities=CAPABILITIES)


def runtime():
    return dict(identity(), pid=os.getpid(), instance_id=os.environ.get('VIGIL_INSTANCE_ID', ''))
