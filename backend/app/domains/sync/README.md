# Sync Domain

Owns sync guard, timer, run-status, and read-only sync sentinel rules.

Provider calls, timer mutations, manual ack writes, and sync execution stay outside this domain.

Current migrated slice:

- `sentinel.py`: pure P7.80 Sync Sentinel signal generation and read-only report assembly.
