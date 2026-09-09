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

$envArgs = @()
foreach ($name in "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SABLE_BACKEND", "SABLE_MODEL", "SABLE_API_BASE") {
    $val = [Environment]::GetEnvironmentVariable($name)
    if ($val) { $envArgs += @("-e", "$name=$val") }
}

docker run -it --rm `
    -v "${root}:/app" `
    -v "sable-playground-home:/root" `
    --add-host=host.docker.internal:host-gateway `
    --cap-add=SYS_ADMIN --security-opt seccomp=unconfined `
    @envArgs $image $Mode
