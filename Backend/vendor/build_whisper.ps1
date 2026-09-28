# Dictation engine → Backend/vendor/whisper/{bin,models}
#
# Builds whisper.cpp at the pinned commit exactly the way the 28 Sep 2026
# measurements were built (Vulkan as a separately loaded backend module, every
# CPU variant, shared libraries), copies the server, its DLLs and the app-local
# VC++ runtime into bin/, and places the pinned q8_0 model into models/.
# electron-builder ships that folder as resources/whisper (electron-builder.yml).
#
# Nothing here runs on a user's machine: the installer carries the result
# (product rule, docs/architecture.md). Binaries and model are git-ignored.
#
# Needs: Visual Studio 2022+ with the C++ workload (its bundled CMake + Ninja are
# used), git, python (scripts/pinned_assets.py), and the Vulkan SDK (glslc). CI
# passes -InstallVulkanSdk to install the pinned SDK; locally VULKAN_SDK or
# C:\VulkanSDK\<pinned version> is used.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File Backend/vendor/build_whisper.ps1
#     [-ModelFile <path to an existing ggml-large-v3-turbo-q8_0.bin>]   # skips the 874 MB download; still verified
#     [-SourceDir <short path>] [-InstallVulkanSdk]
param(
    # Short on purpose: the Vulkan shader generator can hit the 260-character
    # path limit in a deep folder (whisper.cpp / transcribe.cpp build notes).
    [string]$SourceDir = (Join-Path ([IO.Path]::GetTempPath()) 'gm-whisper-d09f61a7'),
    [string]$ModelFile = $env:GAMACHINE_WHISPER_MODEL_FILE,
    [switch]$InstallVulkanSdk
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Exit codes are checked by hand below; PS 7.4+ must not turn a native
# command's stderr into a terminating error on its own.
$PSNativeCommandUseErrorActionPreference = $false

$Commit        = 'd09f61a708f3487afa956ff578e60eae5e7a233c'
$RepoUrl       = 'https://github.com/ggml-org/whisper.cpp.git'
$VulkanVersion = '1.4.357.0'
$ModelKey      = 'whisper-model/large-v3-turbo-q8_0'
$VulkanKey     = 'vulkan-sdk/windows'
$ModelName     = 'ggml-large-v3-turbo-q8_0.bin'

$OutDir    = Join-Path $PSScriptRoot 'whisper'
$BinDir    = Join-Path $OutDir 'bin'
$ModelsDir = Join-Path $OutDir 'models'
$Stamp     = Join-Path $OutDir '.built'
$PinnedCli = (Resolve-Path (Join-Path $PSScriptRoot '..\..\scripts\pinned_assets.py')).Path

function Invoke-Native {
    # Runs a native command and throws on a non-zero exit code. PS 5.1 does not
    # turn native failures into errors by itself.
    param([string]$Exe, [string[]]$CmdArgs)
    & $Exe @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($CmdArgs -join ' ') failed with exit code $LASTEXITCODE" }
}

function Get-PinnedValue([string]$Command, [string]$Key) {
    $v = & python $PinnedCli $Command $Key
    if ($LASTEXITCODE -ne 0) { throw "pinned_assets.py $Command $Key failed (exit $LASTEXITCODE)" }
    return ($v | Out-String).Trim()
}

function Test-Pinned([string]$Key, [string]$Path) {
    & python $PinnedCli verify $Key $Path | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Get-FileFromPin([string]$Key, [string]$Dest) {
    $url = Get-PinnedValue 'url' $Key
    $part = "$Dest.part"
    if (Test-Path $part) { Remove-Item -LiteralPath $part -Force }
    Write-Host "[whisper] downloading $url"
    Invoke-Native 'curl.exe' @('-L', '--fail', '--retry', '3', '-o', $part, $url)
    # A mismatch is never retried: retrying only hands a tampered host more tries.
    if (-not (Test-Pinned $Key $part)) {
        Remove-Item -LiteralPath $part -Force
        throw "$Key does not match its pinned digest; nothing was installed"
    }
    Move-Item -LiteralPath $part -Destination $Dest -Force
}

# ── Visual Studio (compiler, CMake, Ninja, VC++ redist) ─────────────────────
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw 'vswhere.exe not found: install Visual Studio 2022+ with the C++ workload' }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'No Visual Studio with the C++ x64 tools was found' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
$cmakeDir = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin'
$ninjaDir = Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja'
foreach ($p in @($vcvars, $cmakeDir, $ninjaDir)) { if (-not (Test-Path $p)) { throw "Visual Studio component missing: $p" } }

# ── Vulkan SDK ──────────────────────────────────────────────────────────────
$vulkanRoot = "C:\VulkanSDK\$VulkanVersion"
if ($InstallVulkanSdk -and -not (Test-Path (Join-Path $vulkanRoot 'Bin\glslc.exe'))) {
    $installer = Join-Path ([IO.Path]::GetTempPath()) "vulkansdk-$VulkanVersion.exe"
    Get-FileFromPin $VulkanKey $installer
    Write-Host "[whisper] installing Vulkan SDK $VulkanVersion"
    Invoke-Native $installer @('--root', $vulkanRoot, '--accept-licenses', '--default-answer', '--confirm-command', 'install')
    Remove-Item -LiteralPath $installer -Force
}
if ($env:VULKAN_SDK -and (Test-Path (Join-Path $env:VULKAN_SDK 'Bin\glslc.exe'))) {
    $vulkanRoot = $env:VULKAN_SDK
}
if (-not (Test-Path (Join-Path $vulkanRoot 'Bin\glslc.exe'))) {
    throw "Vulkan SDK not found (looked for glslc under $vulkanRoot). Install $VulkanVersion or pass -InstallVulkanSdk."
}
if ((Split-Path $vulkanRoot -Leaf) -ne $VulkanVersion) {
    Write-Warning "Vulkan SDK at $vulkanRoot is not the measured $VulkanVersion; the shaders may differ."
}

# ── Source at the pinned commit ─────────────────────────────────────────────
if (-not (Test-Path (Join-Path $SourceDir '.git'))) {
    New-Item -ItemType Directory -Force -Path $SourceDir | Out-Null
    Invoke-Native 'git' @('-C', $SourceDir, 'init', '-q')
    Invoke-Native 'git' @('-C', $SourceDir, 'remote', 'add', 'origin', $RepoUrl)
}
# A fresh clone has no HEAD yet; its stderr must not stop the script (PS 5.1
# turns redirected native stderr into errors under 'Stop').
$ErrorActionPreference = 'Continue'
$head = (& git -C $SourceDir rev-parse HEAD 2>$null)
$ErrorActionPreference = 'Stop'
if ($head -ne $Commit) {
    Invoke-Native 'git' @('-C', $SourceDir, 'fetch', '-q', '--depth', '1', 'origin', $Commit)
    Invoke-Native 'git' @('-C', $SourceDir, 'checkout', '-q', '--detach', 'FETCH_HEAD')
}
$head = (& git -C $SourceDir rev-parse HEAD)
if ($head -ne $Commit) { throw "whisper.cpp source is at $head, expected $Commit" }
# A local edit would be compiled into what ships; the build dir is untracked
# and ignored by --untracked-files=no.
$dirty = & git -C $SourceDir status --porcelain --untracked-files=no
if ($dirty) { throw "whisper.cpp source at $SourceDir has local modifications:`n$dirty" }

# ── Build (same flags as the measured build, build_wc.bat 28 Sep 2026) ───────
$buildDir = Join-Path $SourceDir 'b'
$jobs = if ($env:NUMBER_OF_PROCESSORS) { $env:NUMBER_OF_PROCESSORS } else { '4' }
# A .cmd file rather than one `cmd /c "..."` string: PowerShell 5.1 re-quotes
# native arguments and mangles the embedded quotes.
$buildCmd = Join-Path ([IO.Path]::GetTempPath()) 'gm-build-whisper.cmd'
$cmdLines = @(
    '@echo off',
    # vcvars64.bat calls vswhere itself and only warns when it is not on PATH.
    "set `"PATH=$(Split-Path $vswhere);%PATH%`"",
    "call `"$vcvars`" >nul || exit /b 10",
    "set `"PATH=$cmakeDir;$ninjaDir;%PATH%`"",
    "set `"VULKAN_SDK=$vulkanRoot`"",
    "cmake -S `"$SourceDir`" -B `"$buildDir`" -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_VULKAN=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=ON -DWHISPER_BUILD_TESTS=OFF || exit /b 11",
    "cmake --build `"$buildDir`" --config Release -j $jobs || exit /b 12",
    'echo VCREDIST=%VCToolsRedistDir%'
)
[IO.File]::WriteAllLines($buildCmd, $cmdLines)
Write-Host '[whisper] configuring and building (about 2 minutes on 12 threads)'
$output = & cmd.exe /d /c $buildCmd
$buildExit = $LASTEXITCODE
Remove-Item -LiteralPath $buildCmd -Force
if ($buildExit -ne 0) { $output | Out-Host; throw "whisper.cpp build failed (exit $buildExit)" }
$redistLine = $output | Where-Object { $_ -like 'VCREDIST=*' } | Select-Object -Last 1
$redist = $redistLine.Substring('VCREDIST='.Length).Trim()
if (-not $redist -or -not (Test-Path $redist)) { throw "VC++ redist folder not found ($redistLine)" }

# ── Stage bin/ ──────────────────────────────────────────────────────────────
$built = Join-Path $buildDir 'bin'
$wanted = @('whisper-server.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-vulkan.dll')
$cpuVariants = @(Get-ChildItem -Path $built -Filter 'ggml-cpu-*.dll')
if ($cpuVariants.Count -eq 0) { throw "no ggml-cpu-*.dll in $built" }
if (Test-Path $BinDir) { Remove-Item -LiteralPath $BinDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
foreach ($f in $wanted) { Copy-Item -LiteralPath (Join-Path $built $f) -Destination $BinDir }
foreach ($f in $cpuVariants) { Copy-Item -LiteralPath $f.FullName -Destination $BinDir }
# App-local VC++ runtime: the four DLLs the binaries import beyond the
# always-present UCRT (dumpbin /dependents, 28 Sep 2026). vcomp140 is OpenMP.
$crt = Get-ChildItem -Path (Join-Path $redist 'x64') -Directory -Filter 'Microsoft.VC*.CRT' | Select-Object -First 1
$omp = Get-ChildItem -Path (Join-Path $redist 'x64') -Directory -Filter 'Microsoft.VC*.OpenMP' | Select-Object -First 1
if (-not $crt -or -not $omp) { throw "VC++ CRT/OpenMP redist folders not found under $redist\x64" }
foreach ($f in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    Copy-Item -LiteralPath (Join-Path $crt.FullName $f) -Destination $BinDir
}
Copy-Item -LiteralPath (Join-Path $omp.FullName 'vcomp140.dll') -Destination $BinDir
Copy-Item -LiteralPath (Join-Path $SourceDir 'LICENSE') -Destination (Join-Path $BinDir 'LICENSE-whisper.cpp.txt')

# Loads whisper.dll, ggml*.dll and the VC++ runtime; a missing import fails here
# instead of on the user's first dictation.
# It logs the Vulkan device to stderr, which PS 5.1 would raise under 'Stop'.
$ErrorActionPreference = 'Continue'
& (Join-Path $BinDir 'whisper-server.exe') --help *> $null
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) { throw "whisper-server.exe --help exited ${LASTEXITCODE}: a DLL is probably missing" }

# ── Model ───────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
$modelDest = Join-Path $ModelsDir $ModelName
if ((Test-Path $modelDest) -and (Test-Pinned $ModelKey $modelDest)) {
    Write-Host '[whisper] model already in place and verified'
} elseif ($ModelFile) {
    if (-not (Test-Pinned $ModelKey $ModelFile)) { throw "$ModelFile does not match the pinned model digest" }
    Copy-Item -LiteralPath $ModelFile -Destination $modelDest -Force
} else {
    Get-FileFromPin $ModelKey $modelDest
}

# ── Stamp: what was built, and the bytes that were installed ─────────────────
$lines = @("commit=$Commit model=$(Get-PinnedValue 'digest' $ModelKey)")
Get-ChildItem -Path $OutDir -Recurse -File | Where-Object { $_.Name -ne '.built' } | Sort-Object FullName | ForEach-Object {
    $rel = $_.FullName.Substring($OutDir.Length + 1).Replace('\', '/')
    $lines += "$rel sha256:$((Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLower())"
}
[IO.File]::WriteAllLines($Stamp, $lines)
Write-Host "[whisper] done: $OutDir"
