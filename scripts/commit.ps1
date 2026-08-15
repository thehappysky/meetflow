param([Parameter(Mandatory=$true)][string]$Message)
$ErrorActionPreference = 'Stop'
# User-selected metadata only; see docs/COMMIT_METADATA.md.
$previousAuthorDate = $env:GIT_AUTHOR_DATE
$previousCommitterDate = $env:GIT_COMMITTER_DATE
try {
    $env:GIT_AUTHOR_DATE = '2026-08-15T10:00:00+08:00'
    $env:GIT_COMMITTER_DATE = '2026-08-15T10:00:00+08:00'
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { throw 'Git commit failed' }
} finally {
    $env:GIT_AUTHOR_DATE = $previousAuthorDate
    $env:GIT_COMMITTER_DATE = $previousCommitterDate
}
