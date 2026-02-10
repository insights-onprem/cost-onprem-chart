# Maintain PR Summary

This prompt helps maintain a running PR summary document that tracks changes as work is developed on a branch.

## Usage

Type `@maintain-pr-summary` in Cursor, then:
- "Update the PR summary with the changes we just made"
- "Create a new PR summary for this branch"
- "Add the bug fix to the PR summary"

## Options

### Default Behavior
- **Location**: Current working directory (project root)
- **Filename**: `PR-SUMMARY-<branch-name>.md` (e.g., `PR-SUMMARY-testetson22-flpath-3075.md`)
- **Gitignored**: Yes (file is automatically ignored)

### Custom Location
Specify a full file path to save the summary elsewhere:
- "Update the PR summary at `/Users/me/workspaces/my-pr-summary.md`"
- "Create PR summary in the workspaces folder"

## What Gets Tracked

The PR summary document includes:

### Header
- Branch name and target branch
- Date of last update
- High-level summary of changes

### Sections
1. **Summary** - Brief description of the PR's purpose with key changes list
2. **Commits** - Table of commits with descriptions
3. **New Test Suites** - Tables of new test files with test counts
4. **Files Changed** - Statistics and categorized tables (new, modified, deleted)
5. **Bug Fixes** - Detailed problem/solution descriptions with affected files
6. **New Markers/Config** - Any new pytest markers or configuration
7. **Architecture** - ASCII diagrams showing test/fixture relationships
8. **CI Impact** - How changes affect CI runs
9. **Related Documents** - Links to relevant docs
10. **Checklist** - Completion status of major items

## Commands

### Create New Summary
```
Create a PR summary for the current branch
```

### Update Existing Summary
```
Update the PR summary with the changes we just made to test_reports.py
```

### Add Specific Section
```
Add a bug fix section to the PR summary for the database name issue
```

### Generate from Git Diff
```
Generate PR summary from git diff against main
```

## Template Structure

```markdown
# PR Summary: <Title>

**Branch**: `<branch-name>`  
**Target**: `main`  
**Last Updated**: <YYYY-MM-DD>

---

## Summary

<Brief description of what this PR accomplishes>

### Key Changes

1. **Category 1**: Description
2. **Category 2**: Description
3. **Category 3**: Description

---

## Commits

| Commit | Description |
|--------|-------------|
| `abc1234` | Commit message summary |
| *(pending)* | Uncommitted changes description |

---

## New Test Suites

### Suite Name (`tests/suites/name/`)

<Brief description of what this suite tests>

| File | Tests | Description |
|------|-------|-------------|
| `test_file.py` | 7 | What these tests cover |

**Total**: X new tests

---

## Files Changed (X files, +Y / -Z lines)

### New Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `path/to/file.py` | 100 | Description |

### Files Modified

| File | Change |
|------|--------|
| `path/to/file.py` | +50/-20 lines - Description of changes |

### Files Deleted

| File | Reason |
|------|--------|
| `path/to/file.py` | Why it was removed |

---

## Bug Fixes

### 1. Issue Title

**Problem**: Description of the bug or issue.

**Solution**: How it was fixed, including approach taken.

**Files Changed**:
- `file1.py`
- `file2.py`

---

## New Pytest Markers

```ini
markers =
    marker_name: Description of what this marker indicates
```

### Usage

```bash
# Example command
pytest -m marker_name
```

---

## Test Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Test Execution                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   suite1/    │    │   suite2/    │    │   suite3/    │      │
│  │              │    │              │    │              │      │
│  │ test_file1   │    │ test_file2   │    │ test_file3   │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │  fixture1    │    │  fixture2    │    │  fixture3    │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    conftest.py                          │   │
│  │            shared_fixture1, shared_fixture2             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Self-Contained Data Fixtures               │   │
│  │  data_fixture1 (suite1/)                                │   │
│  │  data_fixture2 (suite2/)                                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## CI Impact

- **Impact 1**: Description of how this affects CI
- **Impact 2**: Any new requirements or flags
- **Test count**: X tests collected (was Y before changes)

---

## Related Documents

- [Document 1](./path/to/doc.md) - Description
- [Document 2](./path/to/doc.md) - Description

---

## Checklist

- [x] Completed item
- [x] Another completed item
- [ ] Pending item
- [ ] CI validation (pending merge)
```

## Notes

- The summary file is gitignored by default to avoid cluttering PRs
- Use the workspaces folder for summaries you want to persist across sessions
- The summary can be copied into the actual PR description when ready
- Updates are additive - new changes are appended to existing sections
- Run `git diff --stat main` to get accurate line change counts
- Run `pytest --collect-only -q` to get accurate test counts
- Keep the architecture diagram updated as fixtures change
- Mark items in checklist as completed when done