# AGENTS.md

## Repository purpose

This is a public documentation repository for a real homelab. Work must preserve factual accuracy, operational safety, and privacy.

## Instruction refresh

Before any task that will write to GitHub, read the current `AGENTS.md` from the repository default branch. Do not rely on an older chat copy of these rules when the repository version is available.

## Token and context efficiency

Optimize for useful verified work per token. Do not trade away correctness, safety, or required freshness.

- Reuse verified durable state/evidence when still sufficient; do not re-check the same stable fact by default.
- Re-verify only when freshness matters, evidence conflicts, state is stale/unknown, the action is high-risk, implementation may have changed, or Igor asks.
- Avoid duplicate documentation; update the authoritative artifact instead of creating near-copies.
- Prefer concise deltas, exact next actions, and links/references over repeating full history.
- Retrieve narrowly and avoid repeated full-file reads or broad scans when a known range/result is enough.
- Stop research once evidence is sufficient; add more only when it materially improves confidence or coverage.
- Prefer deterministic reusable operations, cached/structured artifacts, and existing Skills where they safely reduce repeated LLM work.

If extra retrieval, verification, or documentation will not materially change the answer, reduce meaningful risk, or create reusable value, skip it.

## Temporal context awareness

When reusing older conversation context, Project State, notes, tool output, or prior plans, account for elapsed time instead of treating every past statement as current.

- Treat time-bound or ephemeral state as potentially stale: today's meal, current pantry/fridge contents, open food or drink, current plans, in-progress tasks, mood, weather, temporary availability, live service status, and similar "now" facts.
- Compare timestamps and relative wording such as `today`, `tomorrow`, `current`, and `now`. Do not silently carry a temporary state forward just because it appears in context.
- Durable facts and decisions may be reused until superseded. Ephemeral facts should be refreshed, qualified as last-known, or re-confirmed when currentness materially affects correctness.
- Prefer wording such as `last known on <date>` when useful rather than presenting stale state as present reality.
- Do not create granular memory, logging, or documentation solely to implement this rule. This is primarily an interpretation rule, not a requirement to store every transient event.

This policy applies across chats, agents, Project State reading, research, planning, and operations.

## Source-of-truth rules

1. Distinguish the current verified host/runtime state from planned or historical infrastructure.
2. Use these status labels consistently:
   - **Verified**: supported by user confirmation, command output, logs, screenshots, or direct inspection.
   - **Component-tested**: an integration test passed, but the complete workflow did not.
   - **Planned**: selected or intended but not implemented.
   - **Blocked**: cannot proceed because a prerequisite is missing.
   - **Unknown**: insufficient evidence; do not infer a value.
   - **Historical**: describes an earlier event, not the current configuration.
3. A newer explicitly confirmed state overrides an older handoff.
4. Never convert a proposal into an implemented claim without new evidence.
5. Prefer live, sanitized command output over remembered configuration when both are available.

## Public-repository safety

Never commit:

- passwords, API keys, tokens, cookies, private keys, or `.env` files;
- usable Plex, TMDb, SABnzbd, indexer, Usenet, or VPN credentials;
- public IP addresses, router configuration, or unnecessary LAN identifiers;
- personal email addresses, account identifiers, invoices, warranty documents, or order numbers;
- full disk serial numbers;
- unsanitized Compose, application configuration, logs, screenshots, or command output.

Use placeholders such as `<HOST_LAN_IP>`, `<PLEX_TOKEN>`, `<TMDB_API_KEY>`, and `<SABNZBD_API_KEY>` in examples.

Run a secret scan before every commit that introduces configuration, logs, or generated output.

## Change safety

- Documentation-only tasks must not connect to or modify the live homelab.
- Do not execute SQL, Docker, filesystem, partitioning, formatting, mount, SMART test, package-management, or network changes unless the task explicitly authorizes live infrastructure work.
- Begin disk work with read-only inventory commands.
- Treat partition deletion, filesystem creation, formatting, burn-in tests, recursive permission changes, and data movement as destructive operations requiring explicit confirmation and exact targets.
- Keep rollback options until acceptance criteria are met and retirement is explicitly decided.

## Codex execution routing

Owner chats such as Plex/Media own scope, priorities and decisions. Codex tasks are
bounded execution units; do not use an old task as the durable project memory.

Use this routing rule:

| Need | Codex lane |
| --- | --- |
| Repository-only docs, tests, analysis or implementation with no live Homelab dependency | Codex Cloud `pab-homelab-dev` |
| Plex/ARR/Docker/service/log/filesystem inspection, deployment, runtime acceptance, or a task likely to need any of them | Codex Desktop Local/Worktree using `local-homelab-admin` |
| Local Git/worktree/task recovery or machine-state reconciliation where the canonical checkout itself matters | Codex Desktop Local on the canonical checkout |

If one bounded task will implement and then deploy or accept on the Homelab, start
with `local-homelab-admin` rather than splitting the same task across Cloud and Local.

The project-local declaration is `.codex/environments/environment.toml`. It is not
a credential store. The Homelab SSH identity remains machine-local and must never
be copied into this repository, a task workspace or Codex Cloud.

Durable implementation truth is pushed GitHub history. Durable status/next action
belongs in the owned Project State. Codex task/worktree state is disposable once
needed work is preserved.

## Codex live homelab access

For tasks that explicitly authorize live homelab access, use the Codex environment's preconfigured SSH host alias `homelab`.

- Treat `homelab` as an environment capability, not repository configuration. Do not commit or infer the underlying hostname/IP, username, key path, private key, or other connection credentials.
- Start with a non-mutating connectivity check such as `ssh -o BatchMode=yes -o ConnectTimeout=10 homelab true` before issuing live commands.
- If the `homelab` alias is unavailable or authentication fails, stop and report that the Codex environment is missing or cannot use the expected connection. Do not create ad-hoc SSH configuration, request secrets in chat, or fall back to hard-coded connection details.
- A new Codex task may reuse this access only when it is started in a published environment that contains the required SSH/access setup. Repository instructions alone do not provide credentials.
- Documentation-only tasks remain subject to the rule above: do not connect to the live homelab unless the task explicitly authorizes it.


### Verified off-LAN operator path — 2026-11-04

The Infrastructure specialist recorded **NordVPN Meshnet** on the Windows
notebook and Ubuntu Homelab, with **interactive SSH accepted from the tested
mobile hotspot only**: 10 consecutive successful TCP/SSH checks, plus 3 after
hotspot reconnect. An earlier intermittent timeout remains unexplained; other
networks and unattended reliability are **not** accepted. Consult the current
[Infrastructure workstream state](.project/workstreams/infrastructure.yaml)
(`remote_access_issue_41`) and [Issue #41](https://github.com/igormego/homelab-infrastructure/issues/41)
for current evidence before treating the route as available.

**Owner-chat discovery:** Plex/Media and other specialists share this repository's
Codex routing rules, but do not automatically inherit each other's live
ChatGPT conversation state. Read this `AGENTS.md` and the appropriate
`.project/workstreams/*.yaml` before choosing a task lane. For an authorized
live Homelab operation from off-LAN, use **Codex Desktop Local**
(`local-homelab-admin`) with the **existing separate machine-local Meshnet SSH
alias** already configured by the operator. Do not assume `ssh homelab`
uses Meshnet; it is the existing LAN alias. Verify the selected alias via
a non-mutating authenticated probe before live work. Never infer or publish its
private HostName, peer address, SSH key, username or host-key details; do not
edit SSH/network configuration merely because an alias is unavailable.

The user's normal ChatGPT/GitHub connector and published Codex Cloud environment
do **not** inherit the notebook's Meshnet connection or administrative SSH key.
This remote path enables connectivity only, not new Codex/agent permissions or
unattended host operations.

## Documentation style

- Write public documentation in English.
- Keep exact facts in tables when useful.
- Prefer portable paths and placeholders over private machine-specific values.
- Link related documents using relative Markdown links.
- Update `docs/current-state.md` and `docs/roadmap.md` when implementation status changes.
- Add an ADR under `docs/decisions/` for durable architectural choices.
- Keep operational commands copy-safe and label any command that can modify state.

## Git workflow

Use a risk-based workflow. A branch is a safety tool, not a mandatory ceremony.

### Routine Sync Lane — direct to `main` allowed

Direct commits to the default branch are allowed for small, deterministic, easily reversible synchronization of already verified reality, including:

- updating this workstream's owned `.project/...yaml` state after a meaningful verified milestone;
- synchronizing `docs/current-state.md`, `docs/roadmap.md`, milestone notes, or other status documentation to verified reality;
- correcting small documentation errors, links, wording, or metadata;
- low-risk documentation-only cleanup that does not change live infrastructure or executable configuration.

Routine Sync Lane must not be used for Docker/Compose behavior changes, scripts, package changes, filesystem/network configuration, credentials handling, deployment behavior, destructive operations, major architecture decisions, or any ambiguous change.

### Change / Review Lane — branch + PR required

Use a dedicated branch and pull request when the change has meaningful operational, implementation, security, or review risk, including:

- Docker/Compose or executable configuration changes;
- scripts or automation changes;
- storage/filesystem/network design changes;
- security, permissions, secrets-management or remote-access changes;
- package/dependency changes;
- destructive or hard-to-reverse changes;
- major architecture changes, ADR-worthy changes, or broad refactors.

Keep unrelated changes out of the branch. Validate links, run applicable tests/checks, and run secret scans where relevant before merge.

### Branch lifecycle ownership

If you create a branch, you own its lifecycle.

- If Igor has already approved the intended change, do not ask for a second approval merely to merge the resulting PR.
- After applicable checks pass and the implemented scope still matches the approved change, merge the PR as part of completing the task.
- Prefer squash merge unless there is a reason to preserve individual commits.
- Delete the merged branch when the available GitHub tooling supports branch deletion.
- If branch deletion is unavailable, explicitly report the leftover merged branch instead of silently leaving cleanup to Igor.
- Do not merge if checks fail, the scope materially changed, or new consequential risk appeared; surface that instead.
- Use draft PRs only for genuinely unfinished work, not by default.

## Project State rules

In a multi-workstream repository, each specialist writes only its assigned workstream state under `.project/workstreams/`. The root `.project/state.yaml` is a project rollup and must not be overwritten by a specialist workstream.

Meaningful verified milestones should synchronize the owned Project State as part of milestone closure. Ordinary discussion, brainstorming, and failed experiments that do not change verified reality do not require a state write.
