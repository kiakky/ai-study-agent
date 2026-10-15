# Initial clean handover only. No force push; no runtime services or private data touched.
param([string]$PythonExecutable = "python")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if ((git branch --show-current) -ne "main") { throw "Expected main branch." }
    if ((git rev-list --count HEAD) -ne "1") { throw "Expected one sanitized initial commit. Use normal reviewed workflows for later commits." }
    if (git status --porcelain) { throw "Publication working tree must be clean." }
    $expectedRemote = "https://github.com/kiakky/ai-study-agent.git"
    if ((git remote get-url origin) -ne $expectedRemote) { throw "Unexpected remote; do not push." }
    & $PythonExecutable scripts/audit_publication.py --initial-snapshot
    if ($LASTEXITCODE -ne 0) { throw "Publication security audit failed." }
    $refs = @(git -c credential.interactive=false ls-remote origin)
    if ($LASTEXITCODE -ne 0) { throw "Sign in with Git Credential Manager first, then rerun this script." }
    if ($refs.Count -ne 0) { throw "Remote is no longer empty. Stop and review; never force-push." }
    # A public destination must never be accepted merely because it is empty.
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri "https://api.github.com/repos/kiakky/ai-study-agent" -TimeoutSec 15
        throw "Remote is anonymously accessible; confirm private visibility before publishing."
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -ne 404) { throw "Remote privacy verification failed; stop before publishing." }
    }
    git push --set-upstream origin main
    if ($LASTEXITCODE -ne 0) { throw "Push failed. Stop and inspect; no force-push retry." }
    $localCommit = git rev-parse HEAD
    $published = @(git -c credential.interactive=false ls-remote origin refs/heads/main)
    if ($LASTEXITCODE -ne 0 -or $published.Count -ne 1 -or ($published[0] -split "\s+")[0] -ne $localCommit) {
        throw "Remote main verification failed; do not report success."
    }
    Write-Output "PASS: remote main matches the audited initial source commit $localCommit"
} finally {
    Pop-Location
}
