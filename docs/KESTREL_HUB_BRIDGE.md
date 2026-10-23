# Kestrel Hub Bridge

The optional Vigil Bridge joins the existing `WS /v1/hub` Relay as a `computer` device. It does not expose Vigil's Brain HTTP API, copy Brain, or execute agent tools for remote callers. It advertises and accepts only the existing `info` action; all other calls receive `not_allowed`.

## Connect

Install `brain/requirements.txt`, then from the Vigil repository run:

```powershell
python brain/kestrel_hub_bridge.py login
python brain/kestrel_hub_bridge.py status
```

Enter the same Relay URL and email used on Kestrel Android. The one-time email code issues a separate `computer` credential, bound to a stable per-installation device ID. The credential file is protected with Windows DPAPI for the current Windows user. Restart Vigil Brain to start the outbound WebSocket. No credential file means no Relay connection or startup request.

To disconnect, run `python brain/kestrel_hub_bridge.py logout`. When reachable, it revokes the current account session and then removes the local DPAPI file. When offline, the local credential is removed; revoke the device from the Relay account's device management after it becomes available.

The Relay sees the account identifier used for email sign-in and the public device name, OS, version, kind, and `info` capability needed for discovery. Model API keys, prompts, chats, files, and tool results are not sent. The Bridge uses the existing Hub call/result frames, keeps the socket alive with WebSocket ping, and reconnects with bounded backoff. The Relay remains a router; the local Brain and its permissions are unchanged.

## Limits

This is the read-only connection stage. Reach `info` returns a small device inventory. Shell, file, screen, task, and other actions are explicitly refused by this Bridge. Do not expose or reverse-proxy Vigil's local HTTP API to the Relay.
