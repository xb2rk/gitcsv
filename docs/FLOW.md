# gitcsv Execution Lifecycle & Data Flow

This document describes the exact sequence of events for all major `gitcsv` operations.

---

## 1. Push Operation Flow

A push transfers local CSV data to remote Google Sheets, provided safety checks pass.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as CLI (push.py)
    participant Sync as SyncEngine
    participant Core as Core Planner
    participant Lock as ProjectLock
    participant DB as SQLite StateDB
    participant Disk as Local CSV File
    participant Sheets as Google Sheets API

    User->>CLI: gitcsv push BossLootData
    CLI->>Sync: plan_push(config, tables)
    Sync->>Disk: read_csv_table()
    Sync->>Sheets: batchGet (read remote values)
    Sync->>DB: get_base()
    Sync->>Core: plan_table(local, remote, base)
    Core-->>Sync: TablePlan
    Sync->>Core: decide_operation(plan, "push")
    Core-->>CLI: OperationDecision (allowed / blocked)

    alt Decision is Blocked (Unbased divergence / Remote modified without --force)
        CLI-->>User: Show blockers and exit (suggest --force)
    else Decision is Allowed
        CLI->>Lock: acquire()
        Lock-->>CLI: Lock acquired (write.lock)

        CLI->>DB: reserve_operation(status='pending', before_images, after_images)
        DB-->>CLI: op_id (UUID)

        CLI->>Sheets: formula_blockers() check
        alt Formulas Detected
            CLI->>DB: cancel_operation(op_id, reason)
            CLI->>Lock: release()
            CLI-->>User: Blocked: Tab contains formulas!
        else Safe to Write
            CLI->>Sheets: ensure_tab_exists()
            CLI->>Sheets: clear() + update() new values
            CLI->>Disk: verify CSV unchanged
            CLI->>Sheets: read back values to verify
            alt Verification Failed / Network Interrupted
                CLI->>DB: fail_operation(op_id, error)
                CLI->>Lock: release()
                CLI-->>User: OperationInterruptedError! Run 'gitcsv recover'
            else Verification OK
                CLI->>DB: commit_operation(op_id, new_base)
                CLI->>Lock: release()
                CLI-->>User: Push verified and committed!
            end
        end
    end
```

---

## 2. Three-Way Merge (Sync) Flow

A `sync` performs an intelligent 3-way merge between local CSV, remote Sheets, and the last synchronized BASE snapshot.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as CLI (sync.py)
    participant Merger as Core 3-Way Merger
    participant Sync as SyncEngine
    participant Disk as Local CSV File
    participant Sheets as Google Sheets API
    participant DB as SQLite StateDB

    User->>CLI: gitcsv sync BossLootData
    CLI->>Sync: scan_tables()
    Sync-->>CLI: TablePlan (with local, remote, and base)

    CLI->>Merger: three_way_merge(plan, strategy="manual")
    alt No Conflicts
        Merger-->>CLI: Merged local & remote snapshots
        CLI->>Sync: apply_table(merged_local, merged_remote)
        Sync->>Disk: write merged CSV
        Sync->>Sheets: write merged Sheet values
        Sync->>DB: commit new base
        CLI-->>User: Sync completed successfully!
    else Cell Conflicts Detected
        Merger-->>CLI: Cell conflicts (same cell edited differently)
        alt Terminal is Interactive (TTY)
            CLI-->>User: Prompt: Choose resolution [ours/theirs/abort]
            User->>CLI: Selects "ours" or "theirs"
            CLI->>Merger: three_way_merge(strategy="ours" or "theirs")
            CLI->>Sync: apply_table(...)
            CLI-->>User: Conflict resolved and synced!
        else Non-interactive (CI / Script)
            CLI-->>User: Exit with error (requires --ours or --theirs flag)
        end
    end
```

---

## 3. Undo & Recovery Lifecycle

### A. The `gitcsv undo` Flow
1. User runs `gitcsv undo TABLE`.
2. `gitcsv` queries `StateDB.get_latest_operation(TABLE)` where `status = 'committed'`.
3. If found, it reads the saved `before_local` and `before_remote` snapshots.
4. Under `ProjectLock`:
   - Writes `before_local` back to disk (`Assets/CSV/TABLE.csv`).
   - Writes `before_remote` back to the Google Sheet tab.
   - Sets the BASE in SQLite to `before_local` (or deletes it if table was new).
5. User gets immediate restoration without manual data surgery.

### B. The `gitcsv recover` Flow
If a write was cut off (e.g. laptop battery died, network socket closed mid-write):
1. The operation remains in `operations` table with `status = 'pending'` or `status = 'failed'`.
2. Running `gitcsv doctor` alerts: `Found 1 pending operation requiring recovery`.
3. Running `gitcsv recover OP_ID --continue` re-applies the planned target snapshots and commits.
4. Running `gitcsv recover OP_ID --restore` rolls back to the before-images and cancels the operation.
