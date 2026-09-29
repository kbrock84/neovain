# Install neovain on Windows:
#
#   irm https://raw.githubusercontent.com/kbrock84/neovain/main/install.ps1 | iex
#
# neovain drives Neovim, so the script also checks for Neovim 0.9 or newer. If it is missing or
# too old, the script asks before installing it. Nothing here needs administrator rights.
#
# $env:NEOVAIN_VERSION       release to install, e.g. v0.1.0 (default: the latest release)
# $env:NEOVAIN_INSTALL_DIR   where to put neovain.exe (default: %LOCALAPPDATA%\Programs\neovain)
# $env:NEOVAIN_NVIM_DIR      where to unpack Neovim (default: %LOCALAPPDATA%\Programs)
# $env:NEOVAIN_INSTALL_NVIM  yes or no: answer the Neovim question ahead of time
# $env:NEOVAIN_ADD_TO_PATH   yes or no: answer the PATH question ahead of time
#
# Works in Windows PowerShell 5.1 and PowerShell 7. The body runs in its own scope so that
# `irm | iex` leaves no variables or settings behind in your session.

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $repo = 'kbrock84/neovain'
    $programs = Join-Path $env:LOCALAPPDATA 'Programs'
    $dir = if ($env:NEOVAIN_INSTALL_DIR) { $env:NEOVAIN_INSTALL_DIR } else { Join-Path $programs 'neovain' }
    $nvimRoot = if ($env:NEOVAIN_NVIM_DIR) { $env:NEOVAIN_NVIM_DIR } else { $programs }

    # Asks a yes/no question. $preset is the matching environment variable: yes or no answers
    # without asking. With no console to ask on, the answer is no.
    function Confirm-Step($question, $preset) {
        if ($preset -match '^(yes|y|1|true)$') { return $true }
        if ($preset -match '^(no|n|0|false)$') { return $false }
        if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
            Write-Host 'No console to ask on, so skipping.'
            return $false
        }
        return (Read-Host "$question [y/N]") -match '^(y|yes)$'
    }

    function Add-UserPath($path) {
        $current = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($null -eq $current) { $current = '' }
        if (($current -split ';') -notcontains $path) {
            $joined = ($current.TrimEnd(';') + ';' + $path).TrimStart(';')
            [Environment]::SetEnvironmentVariable('Path', $joined, 'User')
        }
        if (($env:Path -split ';') -notcontains $path) { $env:Path = "$env:Path;$path" }
    }

    # Directories the user may want on PATH, collected as the script goes.
    $pathDirs = @()

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("neovain-install-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        # ---- neovain ----

        $version = $env:NEOVAIN_VERSION
        if (-not $version) {
            $request = [Net.WebRequest]::Create("https://github.com/$repo/releases/latest")
            $request.AllowAutoRedirect = $false
            $response = $request.GetResponse()
            $version = ($response.Headers['Location'] -split '/tag/')[-1]
            $response.Close()
        }
        if ($version -notmatch '^v[0-9]') { throw "neovain install: could not find a release (got '$version')" }

        $name = "neovain-$version-x86_64-pc-windows-msvc"
        $url = "https://github.com/$repo/releases/download/$version"
        $zip = Join-Path $tmp "$name.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "$url/$name.zip" -OutFile $zip
        Invoke-WebRequest -UseBasicParsing -Uri "$url/SHA256SUMS" -OutFile (Join-Path $tmp 'SHA256SUMS')

        $line = Get-Content (Join-Path $tmp 'SHA256SUMS') | Where-Object { $_ -like "*$name.zip" } | Select-Object -First 1
        if (-not $line) { throw "neovain install: no checksum listed for $name.zip" }
        $expected = ($line -split '\s+')[0]
        $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash
        if ($actual -ne $expected) { throw "neovain install: checksum mismatch for $name.zip" }

        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Copy-Item -Path (Join-Path $tmp "$name\neovain.exe") -Destination (Join-Path $dir 'neovain.exe') -Force
        Write-Host "Installed neovain $version to $(Join-Path $dir 'neovain.exe')"
        if (($env:Path -split ';') -notcontains $dir) { $pathDirs += $dir }

        # ---- Neovim ----

        $found = $null
        $nvim = Get-Command nvim -ErrorAction SilentlyContinue
        if ($nvim) {
            $first = (& $nvim.Source --version 2>$null | Select-Object -First 1)
            if ($first -match '^NVIM v(\d+)\.(\d+)') {
                $found = "$($Matches[1]).$($Matches[2])"
                $newEnough = ([int]$Matches[1] -gt 0) -or ([int]$Matches[2] -ge 9)
            }
        }

        if ($found -and $newEnough) {
            Write-Host "Found Neovim $found"
        } else {
            if ($found) {
                Write-Host "Found Neovim $found, but neovain needs 0.9 or newer."
            } else {
                Write-Host 'Neovim was not found. neovain needs it (0.9 or newer).'
            }
            $nvimDir = Join-Path $nvimRoot 'nvim-win64'
            if (Confirm-Step "Install the latest Neovim to ${nvimDir}? It needs no administrator rights." $env:NEOVAIN_INSTALL_NVIM) {
                $nvimUrl = 'https://github.com/neovim/neovim/releases/latest/download/nvim-win64.zip'
                Write-Host "Downloading $nvimUrl"
                $nvimZip = Join-Path $tmp 'nvim-win64.zip'
                Invoke-WebRequest -UseBasicParsing -Uri $nvimUrl -OutFile $nvimZip
                New-Item -ItemType Directory -Path $nvimRoot -Force | Out-Null
                if (Test-Path $nvimDir) { Remove-Item -Recurse -Force $nvimDir }
                Expand-Archive -Path $nvimZip -DestinationPath $nvimRoot -Force
                $nvimBin = Join-Path $nvimDir 'bin'
                $installed = (& (Join-Path $nvimBin 'nvim.exe') --version | Select-Object -First 1)
                Write-Host "Installed $installed to $nvimDir"
                $pathDirs += $nvimBin
            } else {
                Write-Host 'Install Neovim 0.9 or newer yourself (https://neovim.io/doc/install/), or point'
                Write-Host 'NEOVAIN_NVIM at an nvim.exe you already have.'
            }
        }

        # ---- PATH ----

        if ($pathDirs.Count -gt 0) {
            $list = $pathDirs -join ', '
            if (Confirm-Step "Add $list to your user PATH?" $env:NEOVAIN_ADD_TO_PATH) {
                foreach ($p in $pathDirs) { Add-UserPath $p }
                Write-Host 'Added. New terminals will pick it up.'
            } else {
                Write-Host "Not on your PATH yet: $list"
            }
        }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}
