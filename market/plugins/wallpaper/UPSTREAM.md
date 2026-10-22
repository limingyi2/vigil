# Upstream notice

The embedded Scene/WebGL renderer is adapted from
[elysia395/dsh-wallpaper-engine](https://github.com/elysia395/dsh-wallpaper-engine),
upstream commit `1d38c094a70ed8e573d99b504932c9aa058111b1` (MIT License).

Vigil v4 uses the renderer bundle and WE compatibility behavior as a standalone
plugin. It does not load the DSH host, Cordis routes, React client, or DSH APIs.
The surrounding discovery, capability URLs, settings page, and lifecycle are
Vigil SDK code.
