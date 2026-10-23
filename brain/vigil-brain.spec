# Compatibility entry point. The release pipeline and direct builds share one spec.
import os

canonical = os.path.normpath(os.path.join(SPECPATH, '..', 'deploy', 'brain', 'vigil-brain.spec'))
SPECPATH = os.path.dirname(canonical)
with open(canonical, encoding='utf-8') as source:
    exec(compile(source.read(), canonical, 'exec'))
