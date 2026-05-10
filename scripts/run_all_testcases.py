"""Run recover_pin.te against every testcase save and verify the recovered PIN.

Discovery:
  Walks each search root recursively for files named "8000000000000100".
  A save is considered a testcase iff a "pin.txt" file sits beside it; that
  file holds the expected PIN (digits, optional trailing whitespace).

Verdict per case:
  PASS  - script reports success AND the recovered PIN matches pin.txt
  FAIL  - script reports failure, OR recovered PIN differs from expected

Exit code is 0 only if every discovered testcase passed.

Default roots cover:
  - the worktree's own bundled mock_fs
  - the parent PinCode checkout's mock_fs (if present)
  - the project-wide testcases/ directory (if present)

Pass --root <path> one or more times to override.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPT = 'recover_pin.te'
EMU = 'scripts/te_emulator.py'
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def default_roots(script_dir):
    # The "run root" is the directory containing recover_pin.te — either
    # the PinCode checkout or a worktree of it.
    run_root = os.path.dirname(script_dir)
    while run_root and not os.path.isfile(os.path.join(run_root, SCRIPT)):
        parent = os.path.dirname(run_root)
        if parent == run_root:
            return []
        run_root = parent

    # Always probe the run root's own mock_fs, then walk up looking for
    # known sibling dirs (testcases/, mock_fs/, sibling PinCode/mock_fs).
    # 6 levels covers both <project>/PinCode/scripts and the worktree path
    # <project>/PinCode/.claude/worktrees/<wt>/scripts.
    candidates = {os.path.join(run_root, 'mock_fs')}
    cur = run_root
    for _ in range(6):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
        for sub in ('testcases', 'mock_fs'):
            p = os.path.join(cur, sub)
            if os.path.isdir(p):
                candidates.add(p)
        sibling = os.path.join(cur, 'PinCode', 'mock_fs')
        if os.path.isdir(sibling):
            candidates.add(sibling)

    return sorted(p for p in candidates if os.path.isdir(p))


def discover(roots):
    cases = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            if '8000000000000100' in filenames and 'pin.txt' in filenames:
                save = os.path.join(dirpath, '8000000000000100')
                expected = os.path.join(dirpath, 'pin.txt')
                cases.append((save, expected))
    cases.sort()
    return cases


def run_emulator(save_path):
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = os.path.join(tmp, 'save')
        os.makedirs(save_dir)
        shutil.copy2(save_path, os.path.join(save_dir, '8000000000000100'))
        proc = subprocess.run(
            [sys.executable, EMU, SCRIPT, '--mock-fs', tmp],
            input='1\n\n',
            capture_output=True,
            text=True,
            timeout=120,
        )
    return ANSI.sub('', proc.stdout)


def parse(out):
    success = 'Success! PIN Recovered' in out
    pin = re.search(r'PIN:\s*([0-9]+)', out)
    err = re.search(r'Error: (.+)', out)
    return success, pin.group(1) if pin else None, err.group(1).strip() if err else None


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--root', action='append', default=None,
                        help='Search root (repeatable). Defaults to worktree mock_fs, '
                             'parent PinCode mock_fs, and project testcases/.')
    parser.add_argument('--show-output', action='store_true',
                        help='Print the full emulator output for each case')
    args = parser.parse_args()

    roots = args.root or default_roots(script_dir)

    cases = discover(roots)
    if not cases:
        print('No testcases found. Roots searched:')
        for r in roots:
            print(f'  {r}')
        return 2

    label_root = os.path.commonpath([os.path.abspath(r) for r in roots if os.path.isdir(r)])
    print(f'Running {len(cases)} testcases through {SCRIPT}')
    print('=' * 90)

    fails = []
    for save, expected_path in cases:
        with open(expected_path) as f:
            expected = f.read().strip()
        rel = os.path.relpath(save, label_root).replace('\\', '/')
        try:
            out = run_emulator(save)
        except subprocess.TimeoutExpired:
            print(f'  [TIMEOUT                                            ]  {rel}')
            fails.append((rel, 'timeout'))
            continue

        success, pin, err = parse(out)
        if success and pin == expected:
            verdict = f'PASS PIN={pin}'
        elif success:
            verdict = f'FAIL extracted={pin} expected={expected}'
            fails.append((rel, verdict))
        else:
            verdict = f'FAIL {err or "unknown error"} (expected {expected})'
            fails.append((rel, verdict))

        print(f'  [{verdict:<50}] {rel}')
        if args.show_output:
            print(out)

    print()
    if fails:
        print(f'FAILED: {len(fails)}/{len(cases)}')
        for rel, v in fails:
            print(f'  {rel}: {v}')
        return 1
    print(f'OK: {len(cases)}/{len(cases)} passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
