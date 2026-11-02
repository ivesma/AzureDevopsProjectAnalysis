#!/usr/bin/env bash
#
# Tidy up a local git clone: prune deleted remote branches, remove stale local
# branches and optimise the repository so git stays fast.
#
# Runs on Linux, macOS (including its bash 3.2) and Windows (Git Bash, which
# comes with Git for Windows). Run it from anywhere inside the repository:
#
#   utils/git-cleanup.sh              # show what would happen, then ask
#   utils/git-cleanup.sh --dry-run    # only show what would happen
#   utils/git-cleanup.sh --yes        # don't ask
#
# A local branch is stale when it has been merged into the base branch, or when
# the remote branch it tracked has been deleted ("gone"). Merged branches are
# deleted with `git branch -d`, which git refuses if any work would be lost.
# Gone branches with unmerged commits are only deleted with --force.
#
# The current branch, the base branch, main, master, develop and anything
# matching --keep are never deleted.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: git-cleanup.sh [options]

  -n, --dry-run        show what would be done without changing anything
  -y, --yes            delete stale branches without asking
  -f, --force          also delete gone branches that have unmerged commits
  -b, --base BRANCH    branch that merged work goes into (default: the
                       remote's default branch, else main, else master)
  -k, --keep PATTERN   never delete branches matching this glob, such as
                       'release/*' or 'v*' (can be given more than once)
      --prune-tags     also delete local tags that are not on the remote
      --no-optimise    skip the garbage collection and repacking
      --aggressive     slower, deeper repack and expire unreachable reflog
                       entries now (they can no longer be recovered)
      --tune           set repository config that speeds up everyday git
                       commands (commit graph, untracked cache, fsmonitor)
  -h, --help           show this help
EOF
}

# ------------------------------------------------------------------ output

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m' DIM=$'\033[2m' RED=$'\033[31m' GREEN=$'\033[32m' YELLOW=$'\033[33m' RESET=$'\033[0m'
else
    BOLD='' DIM='' RED='' GREEN='' YELLOW='' RESET=''
fi

step() { printf '\n%s== %s%s\n' "$BOLD" "$1" "$RESET"; }
info() { printf '   %s\n' "$1"; }
warn() { printf '%swarning:%s %s\n' "$YELLOW" "$RESET" "$1" >&2; }
die() { printf '%serror:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# Run a command, or just print it in a dry run.
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '   %swould run: %s%s\n' "$DIM" "$*" "$RESET"
    else
        printf '   %s$ %s%s\n' "$DIM" "$*" "$RESET"
        "$@"
    fi
}

# ------------------------------------------------------------------ options

DRY_RUN=0
ASSUME_YES=0
FORCE=0
BASE=''
KEEP_PATTERNS=''
PRUNE_TAGS=0
OPTIMISE=1
AGGRESSIVE=0
TUNE=0

while [ $# -gt 0 ]; do
    case "$1" in
        -n|--dry-run) DRY_RUN=1 ;;
        -y|--yes) ASSUME_YES=1 ;;
        -f|--force) FORCE=1 ;;
        -b|--base) [ $# -ge 2 ] || die "$1 needs a branch name"; BASE="$2"; shift ;;
        -k|--keep) [ $# -ge 2 ] || die "$1 needs a pattern"; KEEP_PATTERNS="$KEEP_PATTERNS
$2"; shift ;;
        --prune-tags) PRUNE_TAGS=1 ;;
        --no-optimise|--no-optimize) OPTIMISE=0 ;;
        --aggressive) AGGRESSIVE=1 ;;
        --tune) TUNE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# ------------------------------------------------------------------ checks

command -v git >/dev/null 2>&1 || die "git is not installed or not on the PATH"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not inside a git working tree"
cd "$(git rev-parse --show-toplevel)"

for state in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
    if [ -e "$(git rev-parse --git-path "$state")" ]; then
        die "a merge, rebase, cherry-pick, revert or bisect is in progress; finish it first"
    fi
done

case "$(uname -s 2>/dev/null)" in
    Darwin) PLATFORM=macos ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
    *) PLATFORM=linux ;;
esac

# Git version as a comparable number, e.g. 2.39.1 -> 2039.
GIT_VERSION=$(git --version | sed -E 's/^[^0-9]*([0-9]+)\.([0-9]+).*/\1 \2/')
GIT_VERSION=$(( $(echo "$GIT_VERSION" | cut -d' ' -f1) * 1000 + $(echo "$GIT_VERSION" | cut -d' ' -f2) ))

REMOTES=$(git remote)
CURRENT=$(git symbolic-ref --quiet --short HEAD || true)

if [ -z "$BASE" ]; then
    for remote in origin $REMOTES; do
        BASE=$(git symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null || true)
        if [ -n "$BASE" ]; then BASE=${BASE#"$remote"/}; break; fi
    done
fi
if [ -z "$BASE" ]; then
    for candidate in main master; do
        if git show-ref --verify --quiet "refs/heads/$candidate"; then BASE=$candidate; break; fi
    done
fi
[ -n "$BASE" ] || die "could not work out the base branch; pass --base BRANCH"

repo_size() {
    git count-objects -vH | awk -F': ' '/^size-pack/ {p=$2} /^size:/ {l=$2} END {printf "%s packed, %s loose", p, l}'
}

printf '%sRepository:%s %s\n' "$BOLD" "$RESET" "$(pwd)"
printf '%sPlatform:%s   %s, %s\n' "$BOLD" "$RESET" "$PLATFORM" "$(git --version)"
printf '%sBranch:%s     %s (base: %s)\n' "$BOLD" "$RESET" "${CURRENT:-detached HEAD}" "$BASE"
printf '%sSize:%s       %s\n' "$BOLD" "$RESET" "$(repo_size)"
[ "$DRY_RUN" = 1 ] && printf '%sDry run: nothing will be changed.%s\n' "$YELLOW" "$RESET"

# ------------------------------------------------------------------ remotes

step "Fetch and prune remote branches"
if [ -z "$REMOTES" ]; then
    info "no remotes"
elif [ "$PRUNE_TAGS" = 1 ]; then
    run git fetch --all --prune --prune-tags --quiet
else
    run git fetch --all --prune --quiet
fi

# ------------------------------------------------------------------ branches

is_protected() {
    case "$1" in
        "$CURRENT"|"$BASE"|main|master|develop) return 0 ;;
    esac
    local pattern
    set -f
    for pattern in $KEEP_PATTERNS; do
        # shellcheck disable=SC2254  # the pattern is meant to glob
        case "$1" in $pattern) set +f; return 0 ;; esac
    done
    set +f
    return 1
}

# Whether a branch's commits are all in the base branch, locally or on a remote.
is_merged() {
    local ref
    for ref in "refs/heads/$BASE" $(git for-each-ref --format='%(refname)' "refs/remotes/*/$BASE"); do
        if git merge-base --is-ancestor "refs/heads/$1" "$ref" 2>/dev/null; then return 0; fi
    done
    return 1
}

step "Find stale local branches"
SAFE_DELETE=''    # merged: deleted with -d
FORCE_DELETE=''   # gone with unmerged commits: deleted with -D, only with --force
KEPT=''
while IFS='|' read -r branch track; do
    [ -n "$branch" ] || continue
    gone=0
    case "$track" in *gone*) gone=1 ;; esac
    if is_merged "$branch"; then
        reason="merged into $BASE"
        [ "$gone" = 1 ] && reason="$reason, remote branch deleted"
    elif [ "$gone" = 1 ]; then
        reason="remote branch deleted, has unmerged commits"
    else
        continue
    fi
    if [ "$branch" = "$CURRENT" ] || [ "$branch" = "$BASE" ]; then
        continue
    elif is_protected "$branch"; then
        KEPT="$KEPT$branch ($reason)
"
    elif [ "$reason" = "remote branch deleted, has unmerged commits" ]; then
        FORCE_DELETE="$FORCE_DELETE$branch
"
        info "${YELLOW}$branch${RESET} - $reason"
    else
        SAFE_DELETE="$SAFE_DELETE$branch
"
        info "$branch - $reason"
    fi
done <<EOF
$(git for-each-ref --format='%(refname:short)|%(upstream:track)' refs/heads)
EOF

if [ -n "$KEPT" ]; then
    printf '%s' "$KEPT" | while IFS= read -r line; do info "${DIM}keeping $line${RESET}"; done
fi

delete_branches() {  # flag, newline-separated branches
    local flag=$1 branch
    printf '%s' "$2" | while IFS= read -r branch; do
        [ -n "$branch" ] || continue
        # git refuses branches checked out in another worktree; report and carry on.
        run git branch "$flag" "$branch" || warn "could not delete $branch"
    done
}

if [ -z "$SAFE_DELETE" ] && [ -z "$FORCE_DELETE" ]; then
    info "no stale branches"
else
    if [ -n "$FORCE_DELETE" ] && [ "$FORCE" != 1 ]; then
        info "branches in ${YELLOW}yellow${RESET} have work that isn't in $BASE; pass --force to delete them too"
        FORCE_DELETE=''
    fi
    if [ -n "$SAFE_DELETE$FORCE_DELETE" ]; then
        proceed=1
        if [ "$DRY_RUN" != 1 ] && [ "$ASSUME_YES" != 1 ]; then
            if [ -t 0 ]; then
                printf '\n   Delete these branches? [y/N] '
                read -r answer || answer=''
                case "$answer" in [Yy]|[Yy][Ee][Ss]) ;; *) proceed=0 ;; esac
            else
                warn "not running interactively; pass --yes to delete branches"
                proceed=0
            fi
        fi
        if [ "$proceed" = 1 ]; then
            delete_branches -d "$SAFE_DELETE"
            delete_branches -D "$FORCE_DELETE"
        else
            info "branches left alone"
        fi
    fi
fi

# ------------------------------------------------------------------ housekeeping

step "Prune stale worktrees"
run git worktree prune

if [ "$OPTIMISE" = 1 ]; then
    step "Optimise the repository"
    run git pack-refs --all --prune
    if [ "$AGGRESSIVE" = 1 ]; then
        run git reflog expire --expire-unreachable=now --all
        run git gc --aggressive --prune=now --quiet
    else
        run git gc --quiet
    fi
    run git commit-graph write --reachable --changed-paths
    if git config --get rerere.enabled >/dev/null 2>&1; then
        run git rerere gc
    fi
fi

if [ "$TUNE" = 1 ]; then
    step "Tune repository config"
    run git config core.commitGraph true
    run git config gc.writeCommitGraph true
    run git config fetch.writeCommitGraph true
    run git config core.untrackedCache true
    # The built-in file system monitor exists on Windows and macOS from git 2.37.
    if [ "$PLATFORM" != linux ] && [ "$GIT_VERSION" -ge 2037 ]; then
        run git config core.fsmonitor true
    fi
    # Background maintenance (git 2.31+) keeps the repository optimised from now on.
    if [ "$GIT_VERSION" -ge 2031 ]; then
        run git maintenance register || warn "could not register background maintenance"
    fi
fi

step "Done"
info "Size: $(repo_size)"
