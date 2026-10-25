# Branch Atlas

Branch Atlas is a first-party, read-only Git topology viewer for Vigil.

## Use it

1. Run the updated Vigil build with the local Brain on the machine that has Git and the repository.
2. Open **Git 分支图谱** in the lower-right corner.
3. Choose the repository root (the folder containing `.git`), then use **刷新** whenever you want to reload the branch list and recent commit graph.
4. In chat, you can also ask Vigil to inspect a repository if you explicitly provide its absolute local path.

## What it shows

- Current branch and local/remote branch names
- Recent commit topology from `git log --graph --oneline --decorate --all` (up to 80 commits)
- Short working-tree status

The plugin only invokes fixed, read-only Git commands. It does not run `checkout`, `commit`, `pull`, or write project files. Git must be installed and available on the Brain process's `PATH`.

## Local-only boundary

A folder path selected in Electron refers to the **client machine**. A cloud Brain cannot read that path. Use the local Brain on the same machine as the repository; otherwise the plugin reports that the folder is unavailable.

This is an optional directory plugin, not a Creator-generated single-file chat plugin. Its UI assets are reviewed source files in this directory. The source is not bundled into Kestrel; users need an independently published market package to install it on a clean client. Existing installations migrate their legacy copy into the user plugin directory.
