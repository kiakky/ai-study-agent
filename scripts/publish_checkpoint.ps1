# Reviewed sanitized checkpoint only; never force-push or import runtime history.
param([string]$PythonExecutable = "python")
$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if (git status --porcelain) { throw "Commit and review the snapshot first." }
    if ((git remote get-url origin) -ne "https://github.com/kiakky/ai-study-agent.git") { throw "Unexpected remote." }
    & $PythonExecutable scripts/audit_publication.py --history
    if ($LASTEXITCODE -ne 0) { throw "Audit failed." }
    Write-Output "Confirm PRIVATE visibility in authenticated GitHub UI before continuing."
    if ((Read-Host "Type PRIVATE to confirm") -cne "PRIVATE") { throw "Privacy not confirmed." }
    git -c credential.interactive=false fetch origin
    if ($LASTEXITCODE -ne 0) { throw "Authenticate Git Credential Manager; no push attempted." }
    $branch = git branch --show-current
    if (-not $branch) { throw "Detached HEAD." }
    # Ordinary push rejects unrelated/divergent history. Never bypass this protection.
    git push --set-upstream origin "HEAD:refs/heads/$branch"
    if ($LASTEXITCODE -ne 0) { throw "Push rejected; inspect ancestry/auth. Never force-push." }
    $expected = git rev-parse HEAD
    $actual = @(git ls-remote origin "refs/heads/$branch")
    if ($LASTEXITCODE -ne 0 -or $actual.Count -ne 1 -or ($actual[0] -split "\s+")[0] -ne $expected) { throw "Remote verification failed." }
    Write-Output "PASS: audited remote checkpoint $expected"
} finally { Pop-Location }
