$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if (!(Test-Path ".venv/Scripts/python.exe") -or !(Test-Path "frontend/dist/index.html")) {
        throw "Complete the dependency installation and frontend build in README.en.md first."
    }
    $port = if ($env:PORT) { $env:PORT } else { "8765" }
    $env:STUDIO_ORIGIN = "http://127.0.0.1:$port"
    & .venv/Scripts/python.exe -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port $port
    if ($LASTEXITCODE -ne 0) { throw "The local server exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
