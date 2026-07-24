param(
    [string]$OutputDir = "dist",
    [string]$PythonExe = "",
    [switch]$Console,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    (Get-Command python -ErrorAction Stop).Source
}
else {
    (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path
}
$GuideName = -join ([char[]]@(0x4F7F, 0x7528, 0x8BF4, 0x660E, 0x002E, 0x006D, 0x0064))
$DeliverySidecars = @($GuideName, 'THIRD_PARTY_NOTICES.md')
$DeliveryScripts = @(
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
$GraphCandidates = @(
    Get-ChildItem -LiteralPath $Workspace -Directory -ErrorAction Stop |
        ForEach-Object { Join-Path $_.FullName 'knowledge_graph\equipment_selection_graph_v2.json' } |
        Where-Object { Test-Path -LiteralPath $_ }
)
if ($GraphCandidates.Count -ne 1) {
    throw "Expected exactly one authoritative equipment_selection_graph_v2.json, found $($GraphCandidates.Count)."
}
$Graph = $GraphCandidates[0]
$GraphDir = Split-Path -Parent $Graph
$BundleRoot = Join-Path $Root 'build\bundle_assets'
$BundledKnowledge = Join-Path $BundleRoot 'knowledge_graph'
$BundledModelGraph = Join-Path $BundleRoot 'equipment_selection_graph'
$BundledData = Join-Path $BundleRoot 'data'
$BundledSchemas = Join-Path $BundleRoot 'app\schemas'
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
    Assert-ChildPath -ChildPath $Path -ParentPath (Join-Path $Root 'build')
    if (Test-Path -LiteralPath $Path) {
        [IO.Directory]::Delete((ConvertTo-LongPath -Path $Path), $true)
    }
    New-LongPathDirectory -Path $Path
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

Reset-BundleDirectory -Path $BundleRoot
$knowledgeFileCount = Copy-RuntimeKnowledgeAssets `
    -SourceRoot (Join-Path $Root 'knowledge_graph') `
    -DestinationRoot $BundledKnowledge
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
New-Item -ItemType Directory -Path $BundledSchemas -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $Root 'app\schemas') -Filter '*.json' -File |
    Copy-Item -Destination $BundledSchemas -Force

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
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\hash_manifest.csv'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\build_type_option_package.py'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\select_terminal_type.py'),
    (Join-Path $BundledKnowledge 'type_selection\hgt20592_20635\validate_package.py'),
    (Join-Path $BundledModelGraph 'equipment_selection_graph_v2.json'),
    (Join-Path $BundledModelGraph '00-authority-registry.md'),
    (Join-Path $BundledModelGraph '20-model-determination-card.md')
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
Write-Host "  copied equipment tree files: $knowledgeFileCount"
Write-Host "  copied selector package files: $selectorFileCount"
Write-Host "  copied model graph files:     $modelFileCount"
Write-Host "  bundle revision:              $($manifest.bundle_revision)"
Write-Host "  manifest: $BundleManifest"
Write-Host "  source code manifest: $BundledSourceCodeManifest"
if ($PrepareOnly) {
    exit 0
}

Push-Location $Root
try {
    Assert-SourceCodeAuthorityCurrent
    & $Python -m PyInstaller --noconfirm --clean $WindowMode --onefile `
        --name 'EquipmentDesignGraphApp' `
        --icon $AppIcon `
        --distpath $OutputDir `
        --workpath 'build\equipment_design_app' `
        --specpath 'build' `
        --paths (Join-Path $Root 'app') --paths (Join-Path $Root 'scripts') `
        --hidden-import 'pythoncom' --hidden-import 'win32com.client' `
        --hidden-import 'app_core' --hidden-import 'llm_bridge' --hidden-import 'runtime_bundle' --hidden-import 'source_code_manifest' --hidden-import 'aspen_com_import' --hidden-import 'aspen_pfd' --hidden-import 'pfd_canvas' --hidden-import 'tk_gui' --hidden-import 'derivation_workbench' --hidden-import 'user_guide' --hidden-import 'viscosity_fallback' --hidden-import 'tkinterdnd2' --hidden-import 'tkinterdnd2.TkinterDnD' --hidden-import 'equipment_design_agent' --hidden-import 'result_presentation' --hidden-import 'customer_delivery' `
        --hidden-import 'equipment_calc' --hidden-import 'equipment_design_match' --hidden-import 'aspen_equipment_derivation' `
        --add-data "$BundledKnowledge;knowledge_graph" `
        --add-data "$BundledModelGraph;equipment_selection_graph" `
        --add-data "$BundleManifest;." `
        --add-data "$BundledSourceCodeManifest;app" `
        --add-data "$SourceCodeSnapshot;source_code_snapshot" `
        --add-data "$BundledData;data" `
        --add-data "$BundledSchemas;app\schemas" `
        --add-data "$AppAssets;assets" `
        --add-data "$AppFixtures;app\fixtures" `
        (Join-Path $Root 'app\equipment_design_app.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Assert-SourceCodeAuthorityCurrent
    & $Python -m PyInstaller --noconfirm --clean --console --onefile `
        --name 'EquipmentDesignAgentCLI' `
        --icon $AppIcon `
        --distpath $OutputDir `
        --workpath 'build\equipment_design_agent' `
        --specpath 'build' `
        --paths (Join-Path $Root 'app') --paths (Join-Path $Root 'scripts') `
        --hidden-import 'pythoncom' --hidden-import 'win32com.client' `
        --hidden-import 'equipment_design_app' --hidden-import 'app_core' --hidden-import 'llm_bridge' --hidden-import 'runtime_bundle' --hidden-import 'source_code_manifest' --hidden-import 'aspen_com_import' --hidden-import 'aspen_pfd' --hidden-import 'result_presentation' --hidden-import 'customer_delivery' --hidden-import 'viscosity_fallback' `
        --hidden-import 'equipment_calc' --hidden-import 'equipment_design_match' --hidden-import 'aspen_equipment_derivation' `
        --add-data "$BundledKnowledge;knowledge_graph" `
        --add-data "$BundledModelGraph;equipment_selection_graph" `
        --add-data "$BundleManifest;." `
        --add-data "$BundledSourceCodeManifest;app" `
        --add-data "$SourceCodeSnapshot;source_code_snapshot" `
        --add-data "$BundledData;data" `
        --add-data "$BundledSchemas;app\schemas" `
        --add-data "$AppAssets;assets" `
        --add-data "$AppFixtures;app\fixtures" `
        (Join-Path $Root 'app\equipment_design_agent.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Assert-SourceCodeAuthorityCurrent
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
