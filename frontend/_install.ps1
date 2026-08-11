$ErrorActionPreference = 'Continue'
Set-Location 'd:\project\aiknowledge\frontend'
Write-Output ('START: ' + (Get-Date).ToString('o'))
& npm install --no-audit --no-fund --registry=https://registry.npmmirror.com --progress=false
Write-Output ('NPM_EXIT: ' + $LASTEXITCODE)
Write-Output ('END: ' + (Get-Date).ToString('o'))
$exists = Test-Path 'node_modules\react\package.json'
Write-Output ('REACT_INSTALLED: ' + $exists)
