param(
    [string]$OutputDir = "dist",
    [string]$PythonExe = "",
    [string]$BuildDir = "",
    [string]$KnowledgeArchiveDir = "",
    [string]$AppVersion = "2.4.3",
    [switch]$Console,
    [switch]$OneDir,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputRoot = if ([IO.Path]::IsPathRooted($OutputDir)) {
    [IO.Path]::GetFullPath($OutputDir)
}
else {
    [IO.Path]::GetFullPath((Join-Path $Root $OutputDir))
}
$BuildRoot = if ([string]::IsNullOrWhiteSpace($BuildDir)) {
    Join-Path $Root 'build'
}
else {
    [IO.Path]::GetFullPath($BuildDir)
}
$PackageMode = if ($OneDir) { '--onedir' } else { '--onefile' }
$GuiWorkPath = Join-Path $BuildRoot 'equipment_design_app'
$AgentWorkPath = Join-Path $BuildRoot 'equipment_design_agent'
if ($AppVersion -notmatch '^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$') {
    throw "AppVersion must contain three or four numeric components: $AppVersion"
}
$VersionParts = @(
    [int]$Matches[1],
    [int]$Matches[2],
    [int]$Matches[3],
    $(if ($Matches[4]) { [int]$Matches[4] } else { 0 })
)
$NormalizedVersion = $VersionParts -join '.'
$GuiVersionFile = Join-Path $BuildRoot 'EquipmentDesignGraphApp.version.txt'
$AgentVersionFile = Join-Path $BuildRoot 'EquipmentDesignAgentCLI.version.txt'
$Python = if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    (Get-Command python -ErrorAction Stop).Source
}
else {
    (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
}
$GuideName = -join ([char[]]@(0x4F7F, 0x7528, 0x8BF4, 0x660E, 0x002E, 0x006D, 0x0064))
$DeliverySidecars = @($GuideName, 'THIRD_PARTY_NOTICES.md')
$DeliveryScripts = @(
    'audit_llm_multiflow_bridge.py',
    'audit_multi_bkp_model_gate.py',
    'audit_multi_bkp_overview_gate.py',
    'audit_stage1_detailed_reliability.py'
)
foreach ($SidecarName in $DeliverySidecars) {
    $SidecarPath = Join-Path $Root $SidecarName
    if (-not (Test-Path -LiteralPath $SidecarPath -PathType Leaf)) {
        throw "Required delivery sidecar is missing: $SidecarPath"
    }
}
$AppAssets = Join-Path $Root 'app\assets'
$AppFixtures = Join-Path $Root 'app\fixtures'
$AppIcon = Join-Path $AppAssets 'equipment_design_app.ico'
$RequiredIconAssets = @(
    $AppIcon,
    (Join-Path $AppAssets 'equipment_design_app_icon.png'),
    (Join-Path $AppAssets 'equipment_design_icon_source.jpg'),
    (Join-Path $AppAssets 'equipment_design_icon_manifest.json')
)
$MissingIconAssets = @($RequiredIconAssets | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($MissingIconAssets.Count -gt 0) {
    throw "Required application icon asset is missing: $($MissingIconAssets -join ', '). Run scripts\build_app_icon.py first."
}
$TkinterDndVersion = (& $Python -c "import importlib.metadata; print(importlib.metadata.version('tkinterdnd2'))").Trim()
if ($LASTEXITCODE -ne 0 -or $TkinterDndVersion -ne '0.6.2') {
    throw "tkinterdnd2==0.6.2 is required for the packaged drag-and-drop UI. Run: python -m pip install -r requirements-app.txt"
}
$WindowMode = if ($Console) { '--console' } else { '--windowed' }
$Workspace = Split-Path -Parent $Root
$RepositoryGraph = Join-Path $Root 'equipment_selection_graph\equipment_selection_graph_v2.json'
$GraphCandidates = @(
    if (Test-Path -LiteralPath $RepositoryGraph -PathType Leaf) {
        $RepositoryGraph
    }
    else {
        Get-ChildItem -LiteralPath $Workspace -Directory -ErrorAction Stop |
            ForEach-Object { Join-Path $_.FullName 'knowledge_graph\equipment_selection_graph_v2.json' } |
            Where-Object { Test-Path -LiteralPath $_ }
    }
)
if ($GraphCandidates.Count -ne 1) {
    throw "Expected exactly one authoritative equipment_selection_graph_v2.json, found $($GraphCandidates.Count)."
}
$Graph = $GraphCandidates[0]
$GraphDir = Split-Path -Parent $Graph
$KnowledgeArchive = if ([string]::IsNullOrWhiteSpace($KnowledgeArchiveDir)) {
    $null
}
else {
    [IO.Path]::GetFullPath($KnowledgeArchiveDir)
}
if ($KnowledgeArchive -and -not (Test-Path -LiteralPath $KnowledgeArchive -PathType Container)) {
    throw "Knowledge archive directory is missing: $KnowledgeArchive"
}
$BundleRoot = Join-Path $BuildRoot 'bundle_assets'
$BundledKnowledge = Join-Path $BundleRoot 'knowledge_graph'
$BundledModelGraph = Join-Path $BundleRoot 'equipment_selection_graph'
$BundledData = Join-Path $BundleRoot 'data'
$BundledSchemas = Join-Path $BundleRoot 'app\schemas'
$BundledFixtures = Join-Path $BundleRoot 'app\fixtures'
$BundleManifest = Join-Path $BundleRoot 'runtime_asset_manifest.json'
$SourceCodeManifest = Join-Path $Root 'app\source_code_manifest.json'
$BundledSourceCodeManifest = Join-Path $BundleRoot 'app\source_code_manifest.json'
$SourceCodeSnapshot = Join-Path $BundleRoot 'source_code_snapshot'

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$ChildPath,
        [Parameter(Mandatory = $true)][string]$ParentPath
    )
    $parentFull = [IO.Path]::GetFullPath($ParentPath).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $childFull = [IO.Path]::GetFullPath($ChildPath)
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside build root: $childFull"
    }
}

function ConvertTo-LongPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.StartsWith('\\?\')) {
        return $full
    }
    if ($full.StartsWith('\\')) {
        return '\\?\UNC\' + $full.TrimStart('\')
    }
    return '\\?\' + $full
}

function New-LongPathDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    [IO.Directory]::CreateDirectory((ConvertTo-LongPath -Path $Path)) | Out-Null
}

function Copy-LongPathFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-LongPathDirectory -Path (Split-Path -Parent $Destination)
    [IO.File]::Copy(
        (ConvertTo-LongPath -Path $Source),
        (ConvertTo-LongPath -Path $Destination),
        $true
    )
}

function Reset-BundleDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-ChildPath -ChildPath $Path -ParentPath $BuildRoot
    if (Test-Path -LiteralPath $Path) {
        [IO.Directory]::Delete((ConvertTo-LongPath -Path $Path), $true)
    }
    New-LongPathDirectory -Path $Path
}

function New-PyInstallerVersionFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$InternalName,
        [Parameter(Mandatory = $true)][string]$Description
    )
    Assert-ChildPath -ChildPath $Path -ParentPath $BuildRoot
    New-LongPathDirectory -Path (Split-Path -Parent $Path)
    $tuple = $VersionParts -join ', '
    $content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($tuple),
    prodvers=($tuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'Equipment Design Selector'),
          StringStruct('FileDescription', '$Description'),
          StringStruct('FileVersion', '$NormalizedVersion'),
          StringStruct('InternalName', '$InternalName'),
          StringStruct('OriginalFilename', '$InternalName.exe'),
          StringStruct('ProductName', 'Equipment Design Selector'),
          StringStruct('ProductVersion', '$NormalizedVersion')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"@
    Set-Content -LiteralPath $Path -Value $content -Encoding UTF8
}

function Copy-RuntimeKnowledgeAssets {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    $sourceFull = [IO.Path]::GetFullPath($SourceRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $sourceFull -PathType Container)) {
        throw "Knowledge asset root is missing: $sourceFull"
    }
    New-LongPathDirectory -Path $DestinationRoot
    $oldPreference = $ErrorActionPreference
    try {
        # Parser lock/temp directories may be inaccessible. They are not runtime
        # knowledge assets and are filtered below; suppress their traversal noise.
        $ErrorActionPreference = 'SilentlyContinue'
        $sourceFiles = @(
            Get-ChildItem -LiteralPath $sourceFull -Recurse -File -Force |
                Where-Object { $_.Extension.ToLowerInvariant() -in @('.md', '.json', '.csv', '.sqlite') }
        )
    }
    finally {
        $ErrorActionPreference = $oldPreference
    }

    $copiedCount = 0
    foreach ($file in ($sourceFiles | Sort-Object FullName)) {
        $fileFull = [IO.Path]::GetFullPath($file.FullName)
        if (-not $fileFull.StartsWith($sourceFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $relative = $fileFull.Substring($sourceFull.Length).TrimStart([char[]]'\/')
        $parts = @($relative -split '[\\/]')
        $excluded = @($parts | Where-Object {
            $_ -in @('.git', '__pycache__', 'scripts', 'tmp', '_tmp_documents', '_quarantine') -or $_.StartsWith('.')
        })
        if ($excluded.Count -gt 0) {
            continue
        }
        $destination = Join-Path $DestinationRoot $relative
        Copy-LongPathFile -Source $fileFull -Destination $destination
        $copiedCount += 1
    }
    return $copiedCount
}

function Copy-HashManifestPackage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )
    $sourceFull = [IO.Path]::GetFullPath($SourceRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $manifestPath = Join-Path $sourceFull 'hash_manifest.csv'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Required selector hash manifest is missing: $manifestPath"
    }
    $rows = @(Import-Csv -LiteralPath $manifestPath -Encoding UTF8)
    if ($rows.Count -eq 0) {
        throw "Selector hash manifest is empty: $manifestPath"
    }
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    foreach ($row in $rows) {
        $relative = [string]$row.relative_path
        if ([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative)) {
            throw "Invalid selector manifest path: $relative"
        }
        $source = [IO.Path]::GetFullPath((Join-Path $sourceFull $relative))
        if (-not $source.StartsWith($sourceFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Selector manifest path escapes its package: $relative"
        }
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Selector manifest asset is missing: $relative"
        }
        $actualHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $actualSize = (Get-Item -LiteralPath $source).Length
        if ($actualHash -ne [string]$row.sha256 -or $actualSize -ne [int64]$row.size_bytes) {
            throw "Selector manifest asset mismatch: $relative"
        }
        $destination = Join-Path $DestinationRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $DestinationRoot 'hash_manifest.csv') -Force
    return $rows.Count + 1
}

function Assert-SourceCodeAuthorityCurrent {
    & $Python (Join-Path $Root 'app\source_code_manifest.py') verify `
        --root $Root `
        --manifest $SourceCodeManifest
    if ($LASTEXITCODE -ne 0) {
        throw 'Core source code changed after the source-code manifest was frozen.'
    }
    & $Python (Join-Path $Root 'app\source_code_manifest.py') verify `
        --root $SourceCodeSnapshot `
        --manifest $BundledSourceCodeManifest `
        --snapshot
    if ($LASTEXITCODE -ne 0) {
        throw 'Bundled core source snapshot no longer matches its manifest.'
    }
}

function Invoke-PackagedAgentSelftest {
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][string]$RequestPath,
        [Parameter(Mandatory = $true)][string]$ResponsePath,
        [int]$TimeoutMilliseconds = 600000
    )
    if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        throw "Packaged Agent CLI is missing: $ExecutablePath"
    }
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw "Packaged Agent selftest request is missing: $RequestPath"
    }
    Assert-ChildPath -ChildPath $ResponsePath -ParentPath $BuildRoot
    if ($ExecutablePath.Contains('"') -or $RequestPath.Contains('"') -or $ResponsePath.Contains('"')) {
        throw 'Packaged Agent selftest paths must not contain a double quote.'
    }
    if (Test-Path -LiteralPath $ResponsePath -PathType Leaf) {
        [IO.File]::Delete((ConvertTo-LongPath -Path $ResponsePath))
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $ExecutablePath
    $startInfo.Arguments = '--request "{0}" --output "{1}" --pretty' -f $RequestPath, $ResponsePath
    $startInfo.WorkingDirectory = Split-Path -Parent $ExecutablePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    foreach ($name in @(
        'EQUIPMENT_DESIGN_LLM_API_KEY',
        'EQUIPMENT_DESIGN_LLM_BASE_URL',
        'EQUIPMENT_DESIGN_LLM_MODEL_ID'
    )) {
        if ($startInfo.EnvironmentVariables.ContainsKey($name)) {
            $startInfo.EnvironmentVariables.Remove($name)
        }
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'Packaged Agent CLI selftest process did not start.'
        }
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            throw "Packaged Agent CLI selftest timed out after $TimeoutMilliseconds ms."
        }
        $processExitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
    if ($processExitCode -ne 0) {
        throw "PACKAGED_AGENT_SELFTEST_EXIT_NONZERO: $processExitCode"
    }
    if (-not (Test-Path -LiteralPath $ResponsePath -PathType Leaf)) {
        throw "PACKAGED_AGENT_SELFTEST_RESPONSE_MISSING: $ResponsePath"
    }
    try {
        $response = Get-Content -LiteralPath $ResponsePath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "PACKAGED_AGENT_SELFTEST_RESPONSE_INVALID_JSON: $($_.Exception.Message)"
    }
    if (
        $response.ok -ne $true -or
        [int]$response.exit_code -ne 0 -or
        [string]$response.result.status -ne 'PASS'
    ) {
        throw "PACKAGED_AGENT_SELFTEST_RESPONSE_NOT_OK: $($response | ConvertTo-Json -Depth 8 -Compress)"
    }
    Write-Host "Packaged Agent CLI selftest: PASS ($($response.result.check_count) checks)"
}

Reset-BundleDirectory -Path $BundleRoot
New-PyInstallerVersionFile `
    -Path $GuiVersionFile `
    -InternalName 'EquipmentDesignGraphApp' `
    -Description 'Equipment Design Selector Graphical Application'
New-PyInstallerVersionFile `
    -Path $AgentVersionFile `
    -InternalName 'EquipmentDesignAgentCLI' `
    -Description 'Equipment Design Selector Agent CLI'
$archiveKnowledgeFileCount = 0
if ($KnowledgeArchive) {
    $archiveKnowledgeFileCount = Copy-RuntimeKnowledgeAssets `
        -SourceRoot $KnowledgeArchive `
        -DestinationRoot $BundledKnowledge
}
$repositoryKnowledgeFileCount = Copy-RuntimeKnowledgeAssets `
    -SourceRoot (Join-Path $Root 'knowledge_graph') `
    -DestinationRoot $BundledKnowledge
$knowledgeFileCount = $archiveKnowledgeFileCount + $repositoryKnowledgeFileCount
$SelectorRuntimeSource = Join-Path $Root 'knowledge_graph\type_selection\hgt20592_20635'
$SelectorRuntimeDestination = Join-Path $BundledKnowledge 'type_selection\hgt20592_20635'
$selectorFileCount = Copy-HashManifestPackage `
    -SourceRoot $SelectorRuntimeSource `
    -DestinationRoot $SelectorRuntimeDestination
& $Python (Join-Path $SelectorRuntimeDestination 'validate_package.py') --json | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Bundled deterministic connection selector failed its inner package validation.'
}
$modelFileCount = Copy-RuntimeKnowledgeAssets `
    -SourceRoot $GraphDir `
    -DestinationRoot $BundledModelGraph
New-Item -ItemType Directory -Path $BundledData -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $Root 'data\pump_gbt5662_2013_design_points.csv') -Destination $BundledData -Force
Copy-Item -LiteralPath (Join-Path $Root 'data\pipe_gbt12459_2025_dn_od_catalog.csv') -Destination $BundledData -Force
Copy-Item -LiteralPath (Join-Path $Root 'data\database_authority_registry.json') -Destination $BundledData -Force
$BundledDatabaseContracts = Join-Path $BundledData 'database_contracts'
New-Item -ItemType Directory -Path $BundledDatabaseContracts -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $Root 'data\database_contracts') -Filter '*.sql' -File |
    Copy-Item -Destination $BundledDatabaseContracts -Force
New-Item -ItemType Directory -Path $BundledSchemas -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $Root 'app\schemas') -Filter '*.json' -File |
    Copy-Item -Destination $BundledSchemas -Force
New-Item -ItemType Directory -Path $BundledFixtures -Force | Out-Null
$fixtureFiles = @(
    Get-ChildItem -LiteralPath $AppFixtures -File |
        Where-Object { $_.Extension.ToLowerInvariant() -in @('.json', '.md') }
)
foreach ($fixtureFile in $fixtureFiles) {
    Copy-Item -LiteralPath $fixtureFile.FullName -Destination $BundledFixtures -Force
}

& $Python (Join-Path $Root 'app\source_code_manifest.py') create `
    --root $Root `
    --output $SourceCodeManifest `
    --snapshot-root $SourceCodeSnapshot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Copy-Item -LiteralPath $SourceCodeManifest -Destination $BundledSourceCodeManifest -Force
Assert-SourceCodeAuthorityCurrent

$requiredBundleAssets = @(
    (Join-Path $BundledKnowledge 'README.md'),
    (Join-Path $BundledKnowledge 'equipment_match_rules.json'),
    (Join-Path $BundledKnowledge 'equipment_model_recommendation_rules.json'),
    (Join-Path $BundledKnowledge 'equipment_parameter_chain_templates.json'),
    (Join-Path $BundledKnowledge 'equipment_customer_output_profiles.json'),
    (Join-Path $BundledKnowledge 'standards_graph\README.md'),
    (Join-Path $BundledKnowledge 'standards_graph\priority_report_fastmap.md'),
    (Join-Path $BundledKnowledge 'standards_graph\standard_parameter_crosswalk.md'),
    (Join-Path $BundledKnowledge 'standards_graph\source_layer\indexes\chunk_catalog.csv'),
    (Join-Path $BundledKnowledge 'standards_graph\source_layer\indexes\standards_knowledge.sqlite'),
    (Join-Path $BundledKnowledge 'standards_graph\executable_data\build_20260720_visual_batch_v2\executable_store\executable_standard_data.sqlite'),
    (Join-Path $BundledKnowledge 'standards_graph\executable_data\build_20260720_visual_batch_v2\executable_store\build_manifest.json'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\hash_manifest.csv'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\build_type_option_package.py'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\select_terminal_type.py'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\validate_package.py'),
    (Join-Path $BundledModelGraph 'equipment_selection_graph_v2.json'),
    (Join-Path $BundledModelGraph '00-authority-registry.md'),
    (Join-Path $BundledModelGraph '20-model-determination-card.md'),
    (Join-Path $BundledData 'database_authority_registry.json'),
    (Join-Path $BundledDatabaseContracts 'standards_knowledge_public_schema.sql'),
    (Join-Path $BundledDatabaseContracts 'executable_standard_data_public_schema.sql'),
    (Join-Path $BundledFixtures 'agent_selftest_request.json'),
    (Join-Path $BundledFixtures 'all_family_minimum_meaningful_inputs.json')
)
$missingBundleAssets = @($requiredBundleAssets | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missingBundleAssets.Count -gt 0) {
    throw "Runtime knowledge snapshot is incomplete: $($missingBundleAssets -join ', ')"
}

& $Python (Join-Path $Root 'app\runtime_bundle.py') create --root $BundleRoot --output $BundleManifest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python (Join-Path $Root 'app\runtime_bundle.py') verify --root $BundleRoot --required
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$manifest = Get-Content -LiteralPath $BundleManifest -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host "Runtime knowledge snapshot prepared: $($manifest.total_files) files, $([math]::Round($manifest.total_size_bytes / 1MB, 2)) MiB"
Write-Host "  copied archived knowledge files: $archiveKnowledgeFileCount"
Write-Host "  overlaid repository files:       $repositoryKnowledgeFileCount"
Write-Host "  copied knowledge files total:    $knowledgeFileCount"
Write-Host "  copied selector package files: $selectorFileCount"
Write-Host "  copied model graph files:     $modelFileCount"
Write-Host "  copied acceptance fixtures:  $($fixtureFiles.Count)"
Write-Host "  bundle revision:              $($manifest.bundle_revision)"
Write-Host "  manifest: $BundleManifest"
Write-Host "  source code manifest: $BundledSourceCodeManifest"
if ($PrepareOnly) {
    exit 0
}

Push-Location $Root
try {
    Assert-SourceCodeAuthorityCurrent
    & $Python -m PyInstaller --noconfirm --clean $WindowMode $PackageMode `
        --name 'EquipmentDesignGraphApp' `
        --icon $AppIcon `
        --version-file $GuiVersionFile `
        --distpath $OutputDir `
        --workpath $GuiWorkPath `
        --specpath $BuildRoot `
        --paths (Join-Path $Root 'app') --paths (Join-Path $Root 'scripts') `
        --hidden-import 'pythoncom' --hidden-import 'win32com.client' `
        --hidden-import 'app_core' --hidden-import 'llm_bridge' --hidden-import 'runtime_bundle' --hidden-import 'source_code_manifest' --hidden-import 'aspen_com_import' --hidden-import 'aspen_suite' --hidden-import 'aspen_pfd' --hidden-import 'pfd_canvas' --hidden-import 'tk_gui' --hidden-import 'derivation_workbench' --hidden-import 'user_guide' --hidden-import 'viscosity_fallback' --hidden-import 'tkinterdnd2' --hidden-import 'tkinterdnd2.TkinterDnD' --hidden-import 'equipment_design_agent' --hidden-import 'result_presentation' --hidden-import 'customer_delivery' `
        --hidden-import 'equipment_calc' --hidden-import 'equipment_design_match' --hidden-import 'aspen_equipment_derivation' `
        --add-data "$BundledKnowledge;knowledge_graph" `
        --add-data "$BundledModelGraph;equipment_selection_graph" `
        --add-data "$BundleManifest;." `
        --add-data "$BundledSourceCodeManifest;app" `
        --add-data "$SourceCodeSnapshot;source_code_snapshot" `
        --add-data "$BundledData;data" `
        --add-data "$BundledSchemas;app\schemas" `
        --add-data "$AppAssets;assets" `
        --add-data "$BundledFixtures;app\fixtures" `
        (Join-Path $Root 'app\equipment_design_app.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Assert-SourceCodeAuthorityCurrent
    & $Python -m PyInstaller --noconfirm --clean --console $PackageMode `
        --name 'EquipmentDesignAgentCLI' `
        --icon $AppIcon `
        --version-file $AgentVersionFile `
        --distpath $OutputDir `
        --workpath $AgentWorkPath `
        --specpath $BuildRoot `
        --paths (Join-Path $Root 'app') --paths (Join-Path $Root 'scripts') `
        --hidden-import 'pythoncom' --hidden-import 'win32com.client' `
        --hidden-import 'equipment_design_app' --hidden-import 'app_core' --hidden-import 'llm_bridge' --hidden-import 'runtime_bundle' --hidden-import 'source_code_manifest' --hidden-import 'aspen_com_import' --hidden-import 'aspen_suite' --hidden-import 'aspen_pfd' --hidden-import 'result_presentation' --hidden-import 'customer_delivery' --hidden-import 'viscosity_fallback' `
        --hidden-import 'equipment_calc' --hidden-import 'equipment_design_match' --hidden-import 'aspen_equipment_derivation' `
        --add-data "$BundledKnowledge;knowledge_graph" `
        --add-data "$BundledModelGraph;equipment_selection_graph" `
        --add-data "$BundleManifest;." `
        --add-data "$BundledSourceCodeManifest;app" `
        --add-data "$SourceCodeSnapshot;source_code_snapshot" `
        --add-data "$BundledData;data" `
        --add-data "$BundledSchemas;app\schemas" `
        --add-data "$AppAssets;assets" `
        --add-data "$BundledFixtures;app\fixtures" `
        (Join-Path $Root 'app\equipment_design_agent.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Assert-SourceCodeAuthorityCurrent
    $PackagedAgentExecutable = if ($OneDir) {
        Join-Path (Join-Path $OutputRoot 'EquipmentDesignAgentCLI') 'EquipmentDesignAgentCLI.exe'
    }
    else {
        Join-Path $OutputRoot 'EquipmentDesignAgentCLI.exe'
    }
    Invoke-PackagedAgentSelftest `
        -ExecutablePath $PackagedAgentExecutable `
        -RequestPath (Join-Path $BundledFixtures 'agent_selftest_request.json') `
        -ResponsePath (Join-Path $BuildRoot 'EquipmentDesignAgentCLI.selftest.response.json')
    foreach ($SidecarName in $DeliverySidecars) {
        $SidecarPath = Join-Path $Root $SidecarName
        Copy-Item -LiteralPath $SidecarPath -Destination $OutputDir -Force
    }
    $DeliveryScriptDir = Join-Path $OutputDir 'scripts'
    New-Item -ItemType Directory -Path $DeliveryScriptDir -Force | Out-Null
    foreach ($ScriptName in $DeliveryScripts) {
        $ScriptPath = Join-Path (Join-Path $Root 'scripts') $ScriptName
        if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
            throw "Required delivery script is missing: $ScriptPath"
        }
        Copy-Item -LiteralPath $ScriptPath -Destination $DeliveryScriptDir -Force
    }
}
finally {
    Pop-Location
}
