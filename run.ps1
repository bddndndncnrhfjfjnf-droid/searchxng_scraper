# Short commands instead of a long path to python.
# Run from the project folder:
#
#   .\run.ps1 "nasa cosmos"        search, result in results\nasa_cosmos.json
#   .\run.ps1 "nasa cosmos" -v     same, but with a verbose instance-race log
#   .\run.ps1 "nasa" --pdf         PDF-only mode
#   .\run.ps1 test                 offline tests (no network, ~1 minute)
#   .\run.ps1 audit                live audit of every instance
#   .\run.ps1 anubis "query"       live demo of Anubis-gated instances
#   .\run.ps1                      the cheat sheet (same as python main.py)

$py = Join-Path $PSScriptRoot ".venv-lite\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

switch ($args[0]) {
    "test"   { & $py (Join-Path $PSScriptRoot "tests\test_pow_parser.py")
               & $py (Join-Path $PSScriptRoot "tests\test_rotation.py") }
    "audit"  { & $py (Join-Path $PSScriptRoot "tests\live_audit.py") }
    "anubis" { & $py (Join-Path $PSScriptRoot "tests\live_anubis.py") $args[1] }
    default  {
        if ($args.Count -eq 0) {
            & $py (Join-Path $PSScriptRoot "main.py")
        } else {
            $rest = @()
            if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
            & $py (Join-Path $PSScriptRoot "main.py") --input $args[0] @rest
        }
    }
}