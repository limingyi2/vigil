# Deployment environment variables

Deployment and remote-provider test scripts read credentials from environment variables. Keep their values in the local process environment or the target host's protected environment; do not put values in source files, reports, command transcripts, or committed `.env` files.

## Variables

| Variable | Configure it on | Used for | When missing |
| --- | --- | --- | --- |
| `VIGIL_DEPLOY_HOST` | The local PowerShell process that runs a script in `deploy/` | SSH target address for the deployment diagnostics and remote checks | The script exits before opening an SSH connection and names the missing variable only. |
| `VIGIL_DEPLOY_KEY` | The local PowerShell process | Path to the local SSH private-key file used by the five SSH-based scripts: `check_cloud_brain.py`, `check_cloud_config.py`, `diag_wechat_fallback.py`, `fix_cloud_mimo.py`, and `test_cloud_apis.py` | Those scripts exit before connecting and name the missing variable only. The key contents are never read into a log or printed. |
| `VIGIL_BRAIN_TOKEN` | The local PowerShell process | Authorization for Brain checks in `fix_cloud_mimo.py`, `test_chat.py`, and `verify_cloud.py` | The script exits before sending a Brain request and names the missing variable only. |
| `VIGIL_MIMO_API_KEY` | The local PowerShell process for `fix_cloud_mimo.py`; the target host's protected environment for `test_cloud_apis.py` | Provider credential used to configure the MiMo Brain and by the remote MiMo smoke check | The script or remote check stops before changing configuration or making a provider request and reports only the missing variable name. |
| `VIGIL_SILICONFLOW_API_KEY` | The target host's protected environment | Credential used by the remote SiliconFlow provider smoke check in `test_cloud_apis.py` | That remote check stops before making a provider request and reports only the missing variable name. |

## Configure locally

Set values in the current PowerShell process before invoking a deployment script. Use your own protected values; the placeholders below are deliberately nonfunctional:

```powershell
$env:VIGIL_DEPLOY_HOST = '<host configured locally>'
$env:VIGIL_DEPLOY_KEY = '<local path to protected SSH key>'
$env:VIGIL_BRAIN_TOKEN = '<token configured locally>'
$env:VIGIL_MIMO_API_KEY = '<MiMo key configured in a protected secret store>'
```

Configure provider keys on the remote host using its secret manager or protected service environment. Do not pass provider keys as command-line arguments or include them in SSH command text.

## Safety checks

- Check whether a variable is configured without printing its value, for example:

  ```powershell
  [bool]$env:VIGIL_DEPLOY_HOST
  [bool]$env:VIGIL_DEPLOY_KEY
  [bool]$env:VIGIL_BRAIN_TOKEN
  [bool]$env:VIGIL_MIMO_API_KEY
  ```

- For a protected target-host configuration, inspect only variable names and presence flags; do not dump the process environment.
- Confirm missing-variable behavior with an isolated shell where the variable is unset. The scripts should stop with the variable name and must not print its value.
- Keep the private-key file outside the repository with restrictive filesystem permissions. The `VIGIL_DEPLOY_KEY` value is a file path, not key material.
- Before staging deployment changes, scan for address literals, private-key paths, bearer strings, and credential assignments. Review hits without printing credential values.
- Never add handoff notes, local configuration, `.env` files, logs, or key files to a deployment commit.
