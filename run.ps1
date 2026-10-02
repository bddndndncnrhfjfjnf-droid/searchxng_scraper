# Короткие команды вместо длинного пути к python.
# Запуск из папки проекта:
#
#   .\run.ps1 "nasa cosmos"        поиск, результат в results\nasa_cosmos.json
#   .\run.ps1 "nasa cosmos" -v     то же, но с подробным логом гонки инстансов
#   .\run.ps1 "nasa" --pdf         режим «только PDF»
#   .\run.ps1 test                 офлайн-тесты (сеть не нужна, ~1 минута)
#   .\run.ps1 audit                живой аудит всех инстансов
#   .\run.ps1 anubis "запрос"      живое демо инстансов за капчей Anubis
#   .\run.ps1                      памятка (то же, что python main.py)

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