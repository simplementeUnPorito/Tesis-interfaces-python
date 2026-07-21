[CmdletBinding()]
param(
    [string]$StoreRoot = $env:GITHUB_LFS_ROOT,
    [string]$AdapterPath
)

$ErrorActionPreference = 'Stop'
$defaultStoreRoot = 'C:\Users\elias\OneDrive\Github-LFS'
if ([string]::IsNullOrWhiteSpace($StoreRoot)) {
    if (Test-Path -LiteralPath $defaultStoreRoot) { $StoreRoot = $defaultStoreRoot }
    else { throw 'Defina GITHUB_LFS_ROOT o use -StoreRoot con la raíz de Github-LFS.' }
}

$StoreRoot = [IO.Path]::GetFullPath($StoreRoot)
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$repoStorage = Join-Path $StoreRoot 'repositories\Tesis-software-python'
if ([string]::IsNullOrWhiteSpace($AdapterPath)) {
    $command = Get-Command lfs-folderstore -ErrorAction SilentlyContinue
    if ($command) { $AdapterPath = $command.Source }
    else { $AdapterPath = Join-Path $StoreRoot 'tools\lfs-folderstore-v1.0.1\lfs-folderstore-windows-amd64\lfs-folderstore.exe' }
}
if (-not (Test-Path -LiteralPath $AdapterPath -PathType Leaf)) { throw "No se encontró lfs-folderstore en: $AdapterPath" }

New-Item -ItemType Directory -Path (Join-Path $repoStorage 'objects') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $repoStorage 'manifests') -Force | Out-Null
$adapterConfig = [IO.Path]::GetFullPath($AdapterPath).Replace('\', '/')
$storageConfig = [IO.Path]::GetFullPath($repoStorage).Replace('\', '/')
& git -C $repoRoot lfs install --local | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inicializar Git LFS.' }
& git -C $repoRoot config lfs.storage $storageConfig
& git -C $repoRoot config lfs.customtransfer.lfs-folder.path $adapterConfig
& git -C $repoRoot config lfs.customtransfer.lfs-folder.args $storageConfig
& git -C $repoRoot config lfs.customtransfer.lfs-folder.concurrent true
& git -C $repoRoot config lfs.standalonetransferagent lfs-folder
Write-Output "Git LFS configurado para Tesis-software-python en $repoStorage"
