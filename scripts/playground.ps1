# Windows playground launcher (Docker Desktop required).
#   .\scripts\playground.ps1            -> interactive shell in tmux
#   .\scripts\playground.ps1 tests      -> run unit tests inside Linux
#   .\scripts\playground.ps1 bash       -> plain bash in the container
#   .\scripts\playground.ps1 -Rebuild   -> force image rebuild
param(
    [string]$Mode = "",
    [switch]$Rebuild
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$image = "sable-playground"

$exists = docker images -q $image
if ($Rebuild -or -not $exists) {
    docker build -t $image -f "$root/docker/Dockerfile.playground" $root
    if (-not $?) { exit 1 }
}

# Load .env from the repo root, if there is one. A variable already set in the
# shell wins, so `$env:SABLE_MODEL="gpt-4o"; .\scripts\playground.ps1` overrides
# the file for one run without editing it.
$envFile = Join-Path $root ".env"
$fromFile = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $split = $trimmed.IndexOf("=")
        if ($split -lt 1) { continue }
        $name = $trimmed.Substring(0, $split).Trim()
        $value = $trimmed.Substring($split + 1).Trim()
        # Strip one layer of surrounding quotes, which people add out of habit.
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($value -ne "") { $fromFile[$name] = $value }
    }
}

$envArgs = @()
foreach ($name in "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SABLE_BACKEND",
                  "SABLE_MODEL", "SABLE_API_BASE", "SABLE_MOCK_LLM") {
    $val = [Environment]::GetEnvironmentVariable($name)
    if (-not $val) { $val = $fromFile[$name] }
    if ($val) { $envArgs += @("-e", "$name=$val") }
}

docker run -it --rm `
    -v "${root}:/app" `
    -v "sable-playground-home:/root" `
    --add-host=host.docker.internal:host-gateway `
    --cap-add=SYS_ADMIN --security-opt seccomp=unconfined `
    @envArgs $image $Mode
