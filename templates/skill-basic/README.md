# Vigil Skill basic template

This directory is a minimal Node Skill for Vigil's local Skill Host.

Run the static checker before packaging:

```bash
python tools/check_skill_structure.py templates/skill-basic
```

The template uses the host's `setup(api)` contract and registers one safe echo
tool. It has no npm dependencies, does not install packages, and does not
write files or make network requests.
