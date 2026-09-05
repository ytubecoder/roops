# kagami — GitHub credential setup

**Static runbook.** kagami needs GitHub write access to do its job. This is the
one step only Generalissimo can do; everything after it is a normal install.

A console-driven OAuth flow is wanted instead of a hand-pasted token — tracked as
a backlog item, not built. Until then, this is the procedure.

kagami is installed **nowhere** right now (it was uninstalled on llm with the rest; it needs
GitHub write access to do its job: clone `ytubecoder/ytubecoder.github.io`, push branch
`roops/mock-garden-refresh`, open/update ONE PR via `gh pr` when the public mock garden drifts).
The precheck prefers a PAT file and uses it for both `gh` (`GH_TOKEN`) and `git` (x-access-token),
so the narrowest grant is:

1. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → Generate.
   - Resource owner: `ytubecoder`. Repository access: **Only select repositories →
     `ytubecoder/ytubecoder.github.io`** (nothing else).
   - Repository permissions: **Contents: Read and write**, **Pull requests: Read and write**
     (Metadata: Read is added automatically). No account permissions. Expiry: your call (the
     loop will start failing with `auth-failed`/`gh pr` errors when it lapses — visible on the garden).
2. Put it on the guest (the directory already exists, mode 700): from llm run
   `ssh firstparty-svc 'umask 077; cat > ~/.config/roops-kagami/pat'`, paste the token, Ctrl-D.
   Then `ssh firstparty-svc 'wc -c ~/.config/roops-kagami/pat; ls -l ~/.config/roops-kagami/pat'`
   (expect ~90+ bytes, `-rw-------`).
3. Tell the next session "kagami PAT is in place" — it will `git pull` on the guest, run
   `bin/loopctl requirements kagami` (add `file:~/.config/roops-kagami/pat` to kagami's
   `requires=` first, commit, pull), then `LOOPCTL_INSTALL_POLL_TIMEOUT_S=600 bin/loopctl install
   kagami`. The install's self-verify run may open/update the mirror PR — that is its job.
