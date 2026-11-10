# Issue 33 backup prototype

**Draft: production execution is disabled.** No production backup or full
restore has been performed. Do not remove the execution guard until writer
quiescence, recovery after interruption and an isolated application restore
using the actual restored state have been reviewed and tested.

This prototype uses a private host manifest, verifies the USB identity and
streams archives through GPG. It attempts to restart previously running
services after settled failures. An uncertain stop or process death leaves a
durable pending record; it does not restart services automatically. After a
reboot, an operator may run the reviewed root-owned script with
`--recover-services` via interactive sudo. Recovery checks the saved operator,
container identities, service states, stop clients and systemd jobs, and
requires the identified USB volume to be unmounted. It asks for `RECOVER`,
then starts only services recorded as previously active. The pending record is
retained for operator reconciliation; after separately inspecting the USB
generation the operator may rerun recovery and explicitly type `RECONCILED`
to clear only that record. No backup is retried or overwritten.
Uncatchable interruption during USB access can leave the disk mounted.
Encrypted staging and incomplete generations may remain after failure.

The synthetic network-isolated Radarr startup and in-memory checks of restored
SQLite and configuration are separate component tests, not an end-to-end
restore of the actual service. Host-wide consistency is not yet proven.

Before a future approved run, generate a strong recovery phrase and store it
in a password manager available independently of both the server and disk,
or write it on paper and keep it in a secure separate location. Verify access
independently. Never put the phrase in command arguments, environment variables,
logs, shell history or GitHub. A later run additionally requires specific owner
approval for service interruption, writable mount and backup generation.

Run the synthetic unit tests with `python -m unittest discover -s . -p
'test_*.py'`. An actual restore requires a separately approved, isolated Linux
environment and protection for any decrypted credentials.

The workload preflight may exclude at most one additional anonymous local Docker
volume only if its backing directory is empty and no running or stopped container
references it. The volume is never modified or deleted. The same check is repeated
after capture; a nonempty, referenced, inaccessible or changed volume fails closed.
