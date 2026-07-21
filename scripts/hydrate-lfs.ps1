[CmdletBinding(DefaultParameterSetName = 'Selection')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Selection')]
    [string]$Include,
    [Parameter(Mandatory, ParameterSetName = 'All')]
    [switch]$All,
    [string]$StoreRoot = $env:GITHUB_LFS_ROOT
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $PSScriptRoot 'configure-lfs-folderstore.ps1') -StoreRoot $StoreRoot
if ($All) { & git -C $repoRoot lfs checkout }
else { & git -C $repoRoot lfs checkout $Include }
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron hidratar los objetos LFS solicitados.' }
